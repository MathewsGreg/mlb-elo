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

from mlb_elo import db
from mlb_elo.elo import EloEngine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs"
ESPN_SNAPSHOT_PATH = DOCS_DIR / "espn_rpi.json"
OUTPUT_PATH = DOCS_DIR / "data.json"


def replay(games: list[tuple]) -> tuple[EloEngine, int, dict[int, list[dict]]]:
    """Replay games chronologically, recording each team's Elo at the end of
    every date within the current (latest) season only."""
    engine = EloEngine()
    latest_season = max(row[0] for row in games)
    history: dict[int, list[dict]] = {}

    current_date = None
    for (season, official_date, home_id, home_name, away_id, away_name,
         home_score, away_score) in games:
        engine.process_game(
            season=season,
            home_team_id=home_id,
            home_team_name=home_name,
            away_team_id=away_id,
            away_team_name=away_name,
            home_score=home_score,
            away_score=away_score,
        )

        if season == latest_season and official_date != current_date:
            current_date = official_date
            for team_id in engine.ratings:
                history.setdefault(team_id, []).append(
                    {"date": current_date, "elo": round(engine.get_rating(team_id), 1)}
                )

    return engine, latest_season, history


def season_records(conn, season: int) -> dict[str, dict]:
    """Win/loss record per team name for the given season."""
    records: dict[str, dict] = {}
    cur = conn.execute(
        """
        SELECT home_team_name, away_team_name, home_win
        FROM games
        WHERE game_type = 'R' AND status = 'Final' AND season = ?
        """,
        (season,),
    )
    for home_name, away_name, home_win in cur.fetchall():
        home_rec = records.setdefault(home_name, {"wins": 0, "losses": 0})
        away_rec = records.setdefault(away_name, {"wins": 0, "losses": 0})
        if home_win:
            home_rec["wins"] += 1
            away_rec["losses"] += 1
        else:
            home_rec["losses"] += 1
            away_rec["wins"] += 1
    return records


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
               away_team_id, away_team_name, home_score, away_score
        FROM games
        WHERE game_type = 'R' AND status = 'Final'
        ORDER BY official_date, game_pk
        """
    )
    games = cur.fetchall()

    engine, latest_season, history = replay(games)
    records = season_records(conn, latest_season)
    espn = load_espn_snapshot()
    conn.close()

    standings = engine.standings()  # [(name, rating), ...] sorted desc
    teams = []
    for elo_rank, (name, rating) in enumerate(standings, start=1):
        record = records.get(name, {"wins": 0, "losses": 0})
        espn_info = espn["by_name"].get(name)
        team_id = next(tid for tid, tname in engine.team_names.items() if tname == name)
        teams.append({
            "team": name,
            "elo": round(rating, 1),
            "elo_rank": elo_rank,
            "wins": record["wins"],
            "losses": record["losses"],
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
    }

    DOCS_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Replayed {len(games)} games through {latest_season}.")
    print(f"Wrote {OUTPUT_PATH} ({len(teams)} teams).")


if __name__ == "__main__":
    main()
