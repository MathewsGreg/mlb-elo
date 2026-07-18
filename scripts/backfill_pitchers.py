"""Backfill starting-pitcher info onto already-stored 2026 games.

Re-pulls the 2026 schedule with probablePitcher hydration and updates the
matching rows in the games table (which fetch_history.py doesn't populate).

Usage:
    python scripts/backfill_pitchers.py
"""
from datetime import date

import requests

from mlb_elo import db

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
SEASON = 2026


def fetch_probable_pitchers(start_date: str, end_date: str) -> list[dict]:
    resp = requests.get(SCHEDULE_URL, params={
        "sportId": 1,
        "startDate": start_date,
        "endDate": end_date,
        "gameType": "R",
        "hydrate": "probablePitcher",
    }, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    rows = []
    for date_entry in payload.get("dates", []):
        for game in date_entry.get("games", []):
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            home_p = home.get("probablePitcher") or {}
            away_p = away.get("probablePitcher") or {}
            if not home_p and not away_p:
                continue
            rows.append({
                "game_pk": game["gamePk"],
                "home_pitcher_id": home_p.get("id"),
                "home_pitcher_name": home_p.get("fullName"),
                "away_pitcher_id": away_p.get("id"),
                "away_pitcher_name": away_p.get("fullName"),
            })
    return rows


def main() -> None:
    rows = fetch_probable_pitchers(f"{SEASON}-01-01", date.today().isoformat())
    conn = db.connect()
    db.set_probable_pitchers(conn, rows)
    conn.close()
    print(f"Updated probable pitchers on {len(rows)} games.")


if __name__ == "__main__":
    main()
