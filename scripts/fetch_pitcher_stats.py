"""Pull 2026 game logs for every pitcher who has started a game this season.

One API call per pitcher (not per start) via the gameLog stat group, so this
stays cheap even backfilling a full season.

Usage:
    python scripts/fetch_pitcher_stats.py
"""
import requests

from mlb_elo import db

STATS_URL = "https://statsapi.mlb.com/api/v1/people/{person_id}/stats"
SEASON = 2026


def unique_starters(conn) -> set[int]:
    cur = conn.execute(
        """
        SELECT DISTINCT home_pitcher_id FROM games
        WHERE season = ? AND home_pitcher_id IS NOT NULL
        UNION
        SELECT DISTINCT away_pitcher_id FROM games
        WHERE season = ? AND away_pitcher_id IS NOT NULL
        """,
        (SEASON, SEASON),
    )
    return {row[0] for row in cur.fetchall()}


def fetch_game_log(pitcher_id: int) -> list[dict]:
    resp = requests.get(
        STATS_URL.format(person_id=pitcher_id),
        params={"stats": "gameLog", "group": "pitching", "season": SEASON},
        timeout=30,
    )
    resp.raise_for_status()
    stats = resp.json().get("stats", [])
    if not stats:
        return []

    rows = []
    for split in stats[0].get("splits", []):
        stat = split["stat"]
        rows.append({
            "pitcher_id": pitcher_id,
            "game_date": split["date"],
            "outs": stat.get("outs", 0),
            "walks": stat.get("baseOnBalls", 0),
            "hit_by_pitch": stat.get("hitBatsmen", 0),
            "home_runs": stat.get("homeRuns", 0),
            "strikeouts": stat.get("strikeOuts", 0),
        })
    return rows


def main() -> None:
    conn = db.connect()
    pitcher_ids = unique_starters(conn)

    total_rows = 0
    for i, pitcher_id in enumerate(sorted(pitcher_ids), start=1):
        rows = fetch_game_log(pitcher_id)
        if rows:
            db.upsert_pitcher_game_logs(conn, rows)
            total_rows += len(rows)
        if i % 25 == 0:
            print(f"  {i}/{len(pitcher_ids)} pitchers...")

    conn.close()
    print(f"Fetched game logs for {len(pitcher_ids)} pitchers, {total_rows} starts logged.")


if __name__ == "__main__":
    main()
