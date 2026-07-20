"""Log a home-win-probability prediction for each of a date's not-yet-started
games, using current Elo ratings and starting-pitcher FIP as of that date.

Predictions are write-once: a game already in the predictions table is left
untouched, so grading later reflects what this model actually said beforehand,
never hindsight. Run this once per day, before the day's games start.

Usage:
    python scripts/predict.py [YYYY-MM-DD]   # defaults to today
"""
import sys
from datetime import date, datetime, timezone

import requests

from mlb_elo import db, fip
from mlb_elo.elo import EloEngine, predict_win_prob

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def current_ratings(conn) -> EloEngine:
    """Replay all final games to get each team's rating right now — same
    replay every other script in this project does."""
    cur = conn.execute(
        """
        SELECT season, official_date, home_team_id, home_team_name,
               away_team_id, away_team_name, home_score, away_score,
               home_pitcher_id, away_pitcher_id
        FROM games
        WHERE game_type = 'R' AND status = 'Final'
        ORDER BY official_date, game_pk
        """
    )
    games = cur.fetchall()
    latest_season = max(row[0] for row in games)

    engine = EloEngine()
    for (season, official_date, home_id, home_name, away_id, away_name,
         home_score, away_score, home_pitcher_id, away_pitcher_id) in games:
        home_fip = away_fip = None
        if season == latest_season and home_pitcher_id and away_pitcher_id:
            home_fip = fip.fip_as_of(conn, home_pitcher_id, official_date)
            away_fip = fip.fip_as_of(conn, away_pitcher_id, official_date)

        engine.process_game(
            season=season,
            home_team_id=home_id,
            home_team_name=home_name,
            away_team_id=away_id,
            away_team_name=away_name,
            home_score=home_score,
            away_score=away_score,
            home_pitcher_fip=home_fip,
            away_pitcher_fip=away_fip,
        )
    return engine


def fetch_not_yet_started(game_date: str) -> list[dict]:
    resp = requests.get(SCHEDULE_URL, params={
        "sportId": 1,
        "date": game_date,
        "gameType": "R",
        "hydrate": "probablePitcher",
    }, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    games = []
    for date_entry in payload.get("dates", []):
        for game in date_entry.get("games", []):
            if game["status"]["abstractGameState"] == "Final":
                continue
            games.append(game)
    return games


def main() -> None:
    game_date = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

    conn = db.connect()
    engine = current_ratings(conn)
    games = fetch_not_yet_started(game_date)
    predicted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = []
    for game in games:
        home = game["teams"]["home"]
        away = game["teams"]["away"]
        home_id, home_name = home["team"]["id"], home["team"]["name"]
        away_id, away_name = away["team"]["id"], away["team"]["name"]

        home_pitcher_id = (home.get("probablePitcher") or {}).get("id")
        away_pitcher_id = (away.get("probablePitcher") or {}).get("id")
        home_fip = fip.fip_as_of(conn, home_pitcher_id, game_date) if home_pitcher_id else None
        away_fip = fip.fip_as_of(conn, away_pitcher_id, game_date) if away_pitcher_id else None

        prob = predict_win_prob(
            engine.get_rating(home_id), engine.get_rating(away_id), home_fip, away_fip
        )

        rows.append({
            "game_pk": game["gamePk"],
            "predicted_at": predicted_at,
            "official_date": game_date,
            "season": int(game["season"]),
            "home_team_id": home_id,
            "home_team_name": home_name,
            "away_team_id": away_id,
            "away_team_name": away_name,
            "home_elo_prob": round(prob, 4),
            "home_pitcher_fip": home_fip,
            "away_pitcher_fip": away_fip,
            "vegas_home_prob": None,
        })

    db.insert_predictions(conn, rows)
    conn.close()
    print(f"{game_date}: logged {len(rows)} predictions (existing ones for this date untouched).")


if __name__ == "__main__":
    main()
