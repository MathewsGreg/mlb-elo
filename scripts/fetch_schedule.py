"""Smoke test: pull today's MLB schedule from the live MLB Stats API.

Usage:
    python scripts/fetch_schedule.py [YYYY-MM-DD]

With no argument, uses today's date.
"""
import sys
from datetime import date

import requests

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def fetch_schedule(game_date: str) -> list[dict]:
    params = {
        "sportId": 1,  # MLB
        "date": game_date,
        "hydrate": "probablePitcher",
    }
    resp = requests.get(SCHEDULE_URL, params=params, timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    games = []
    for date_entry in payload.get("dates", []):
        games.extend(date_entry.get("games", []))
    return games


def format_game(game: dict) -> str:
    away = game["teams"]["away"]["team"]["name"]
    home = game["teams"]["home"]["team"]["name"]
    status = game["status"]["detailedState"]
    game_time = game.get("gameDate", "")

    away_pitcher = game["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
    home_pitcher = game["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")

    return (
        f"{away} @ {home}  ({status})\n"
        f"    {game_time}\n"
        f"    Probable: {away_pitcher} vs. {home_pitcher}"
    )


def main() -> None:
    game_date = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

    games = fetch_schedule(game_date)
    if not games:
        print(f"No MLB games scheduled for {game_date}.")
        return

    print(f"MLB schedule for {game_date} - {len(games)} game(s):\n")
    for game in games:
        print(format_game(game))
        print()


if __name__ == "__main__":
    main()
