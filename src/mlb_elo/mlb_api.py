"""Thin client for the free MLB Stats API (statsapi.mlb.com)."""
import requests

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_SPORT_ID = 1


def fetch_schedule(start_date: str, end_date: str, game_type: str = "R") -> list[dict]:
    """Return raw game dicts for the given date range (inclusive)."""
    params = {
        "sportId": MLB_SPORT_ID,
        "startDate": start_date,
        "endDate": end_date,
        "gameType": game_type,
    }
    resp = requests.get(SCHEDULE_URL, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    games = []
    for date_entry in payload.get("dates", []):
        games.extend(date_entry.get("games", []))
    return games


def parse_final_game(game: dict) -> dict | None:
    """Convert a raw schedule game dict into a storage row, or None if not final."""
    if game["status"]["abstractGameState"] != "Final":
        return None

    away = game["teams"]["away"]
    home = game["teams"]["home"]
    if "score" not in away or "score" not in home:
        return None

    return {
        "game_pk": game["gamePk"],
        "official_date": game["officialDate"],
        "game_datetime": game["gameDate"],
        "season": int(game["season"]),
        "game_type": game["gameType"],
        "away_team_id": away["team"]["id"],
        "away_team_name": away["team"]["name"],
        "home_team_id": home["team"]["id"],
        "home_team_name": home["team"]["name"],
        "away_score": away["score"],
        "home_score": home["score"],
        "home_win": int(bool(home.get("isWinner"))),
        "venue_id": game.get("venue", {}).get("id"),
        "venue_name": game.get("venue", {}).get("name"),
        "status": game["status"]["detailedState"],
    }
