"""Pull regular-season MLB results for one or more seasons into data/mlb.db.

Usage:
    python scripts/fetch_history.py 2023 2024 2025 2026
"""
import sys

from mlb_elo import db, mlb_api


def fetch_season(season: int) -> list[dict]:
    end_date = f"{season}-12-31"
    if season == 2026:
        # Current season: don't ask the API for dates past today.
        from datetime import date
        end_date = date.today().isoformat()

    raw_games = mlb_api.fetch_schedule(f"{season}-01-01", end_date, game_type="R")
    rows = [row for g in raw_games if (row := mlb_api.parse_final_game(g)) is not None]
    return rows


def main() -> None:
    seasons = [int(s) for s in sys.argv[1:]] or [2026]

    conn = db.connect()
    for season in seasons:
        rows = fetch_season(season)
        db.upsert_games(conn, rows)
        print(f"Season {season}: upserted {len(rows)} final games.")
    conn.close()


if __name__ == "__main__":
    main()
