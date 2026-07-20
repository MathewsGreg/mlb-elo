"""Replay all stored games through the Elo engine and export dashboard data.

Writes docs/data.json: current standings, each team's Elo trajectory through
the current season, and a rank comparison against the ESPN RPI snapshot in
docs/espn_rpi.json.

Usage:
    python scripts/export_dashboard.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from mlb_elo import db, fip
from mlb_elo.elo import EloEngine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs"
ESPN_SNAPSHOT_PATH = DOCS_DIR / "espn_rpi.json"
OUTPUT_PATH = DOCS_DIR / "data.json"


def replay(conn, games: list[tuple]) -> tuple[EloEngine, int, dict[int, list[dict]]]:
    """Replay games chronologically, recording each team's Elo at the end of
    every date within the current (latest) season only. Starting-pitcher FIP
    (as of the game date, so no look-ahead) adjusts the current season's
    games where both starters have a usable sample; earlier seasons and
    games missing pitcher data fall back to team-strength-only."""
    engine = EloEngine()
    latest_season = max(row[0] for row in games)
    history: dict[int, list[dict]] = {}

    current_date = None
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

        if season == latest_season and official_date != current_date:
            current_date = official_date
            for team_id in engine.ratings:
                history.setdefault(team_id, []).append(
                    {"date": current_date, "elo": round(engine.get_rating(team_id), 1)}
                )

    return engine, latest_season, history


def season_records(conn, season: int) -> dict[str, dict]:
    """Win/loss record and runs scored/allowed per team name for the season."""
    records: dict[str, dict] = {}
    cur = conn.execute(
        """
        SELECT home_team_name, away_team_name, home_win, home_score, away_score
        FROM games
        WHERE game_type = 'R' AND status = 'Final' AND season = ?
        """,
        (season,),
    )
    for home_name, away_name, home_win, home_score, away_score in cur.fetchall():
        home_rec = records.setdefault(home_name, {"wins": 0, "losses": 0, "runs_scored": 0, "runs_allowed": 0})
        away_rec = records.setdefault(away_name, {"wins": 0, "losses": 0, "runs_scored": 0, "runs_allowed": 0})
        home_rec["runs_scored"] += home_score
        home_rec["runs_allowed"] += away_score
        away_rec["runs_scored"] += away_score
        away_rec["runs_allowed"] += home_score
        if home_win:
            home_rec["wins"] += 1
            away_rec["losses"] += 1
        else:
            home_rec["losses"] += 1
            away_rec["wins"] += 1
    return records


def upcoming_predictions(conn) -> list[dict]:
    """Logged predictions for games that haven't been played yet. games only
    ever stores rows for Final games (see mlb_api.parse_final_game), so a
    not-yet-played game has no games row at all — left join, not inner."""
    cur = conn.execute(
        """
        SELECT p.official_date, p.away_team_name, p.home_team_name, p.home_elo_prob, p.vegas_home_prob
        FROM predictions p
        LEFT JOIN games g ON g.game_pk = p.game_pk
        WHERE g.status IS NULL OR g.status != 'Final'
        ORDER BY p.official_date, p.game_pk
        """
    )
    return [
        {
            "date": date_,
            "away_team": away,
            "home_team": home,
            "home_elo_prob": round(home_prob, 3),
            "vegas_home_prob": round(vegas_prob, 3) if vegas_prob is not None else None,
        }
        for date_, away, home, home_prob, vegas_prob in cur.fetchall()
    ]


def prediction_scorecard(conn) -> dict:
    """Score every graded (game now Final) prediction against what actually
    happened: straight-up record and Brier score. home_elo_prob was logged
    before the game via predict.py and is never overwritten, so this is a
    true out-of-sample record, not hindsight."""
    cur = conn.execute(
        """
        SELECT p.official_date, p.away_team_name, p.home_team_name,
               p.home_elo_prob, p.vegas_home_prob, g.home_win
        FROM predictions p
        JOIN games g ON g.game_pk = p.game_pk
        WHERE g.status = 'Final'
        ORDER BY p.official_date, p.game_pk
        """
    )
    rows = cur.fetchall()

    graded = []
    correct = 0
    brier_sum = 0.0

    # Elo vs. Vegas, computed only over the subset of graded games that
    # actually have a logged line -- comparing full-history Elo Brier
    # against Vegas-subset Elo Brier would be apples to oranges.
    vegas_n = 0
    vegas_correct = 0
    vegas_brier_sum = 0.0
    elo_subset_correct = 0
    elo_subset_brier_sum = 0.0

    for date_, away, home, home_prob, vegas_prob, home_win in rows:
        picked_home = home_prob >= 0.5
        won_pick = bool(picked_home) == bool(home_win)
        correct += int(won_pick)
        brier_sum += (home_prob - home_win) ** 2

        if vegas_prob is not None:
            vegas_n += 1
            vegas_picked_home = vegas_prob >= 0.5
            vegas_correct += int(bool(vegas_picked_home) == bool(home_win))
            vegas_brier_sum += (vegas_prob - home_win) ** 2
            elo_subset_correct += int(won_pick)
            elo_subset_brier_sum += (home_prob - home_win) ** 2

        graded.append({
            "date": date_,
            "away_team": away,
            "home_team": home,
            "home_elo_prob": round(home_prob, 3),
            "vegas_home_prob": round(vegas_prob, 3) if vegas_prob is not None else None,
            "home_won": bool(home_win),
            "correct": won_pick,
        })

    n = len(rows)
    return {
        "graded_games": n,
        "correct": correct,
        "incorrect": n - correct,
        "brier_score": round(brier_sum / n, 4) if n else None,
        "vegas_comparison": {
            "graded_games": vegas_n,
            "elo_correct": elo_subset_correct,
            "elo_brier_score": round(elo_subset_brier_sum / vegas_n, 4),
            "vegas_correct": vegas_correct,
            "vegas_brier_score": round(vegas_brier_sum / vegas_n, 4),
        } if vegas_n else None,
        "recent": graded[-20:],
    }


def load_espn_snapshot() -> dict:
    with open(ESPN_SNAPSHOT_PATH, encoding="utf-8") as f:
        snapshot = json.load(f)
    ranked = sorted(snapshot["teams"], key=lambda t: t["rpi"], reverse=True)
    by_name = {}
    for rank, team in enumerate(ranked, start=1):
        by_name[team["team"]] = {"rpi": team["rpi"], "rpi_rank": rank}
    return {
        "by_name": by_name,
        "source": snapshot["source"],
        "source_label": snapshot["source_label"],
        "as_of": snapshot["as_of"],
    }


def main() -> None:
    conn = db.connect()
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

    engine, latest_season, history = replay(conn, games)
    records = season_records(conn, latest_season)
    espn = load_espn_snapshot()
    upcoming = upcoming_predictions(conn)
    scorecard = prediction_scorecard(conn)
    conn.close()

    standings = engine.standings()  # [(name, rating), ...] sorted desc
    teams = []
    default_record = {"wins": 0, "losses": 0, "runs_scored": 0, "runs_allowed": 0}
    for elo_rank, (name, rating) in enumerate(standings, start=1):
        record = records.get(name, default_record)
        espn_info = espn["by_name"].get(name)
        team_id = next(tid for tid, tname in engine.team_names.items() if tname == name)
        games_played = record["wins"] + record["losses"]
        teams.append({
            "team": name,
            "elo": round(rating, 1),
            "elo_rank": elo_rank,
            "wins": record["wins"],
            "losses": record["losses"],
            "runs_scored": record["runs_scored"],
            "runs_allowed": record["runs_allowed"],
            # A game is ~9 innings, so runs/game already is the per-9 rate.
            "runs_scored_per9": round(record["runs_scored"] / games_played, 2) if games_played else None,
            "runs_allowed_per9": round(record["runs_allowed"] / games_played, 2) if games_played else None,
            "rpi": espn_info["rpi"] if espn_info else None,
            "rpi_rank": espn_info["rpi_rank"] if espn_info else None,
            "delta": (espn_info["rpi_rank"] - elo_rank) if espn_info else None,
            "history": history.get(team_id, []),
        })

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": latest_season,
        "games_replayed": len(games),
        "espn_source": espn["source"],
        "espn_source_label": espn["source_label"],
        "espn_as_of": espn["as_of"],
        "teams": teams,
        "upcoming_predictions": upcoming,
        "prediction_scorecard": scorecard,
    }

    DOCS_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Replayed {len(games)} games through {latest_season}.")
    print(f"Wrote {OUTPUT_PATH} ({len(teams)} teams).")


if __name__ == "__main__":
    main()
