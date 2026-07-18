"""Replay all stored games chronologically through the Elo engine and print
current ratings.

Usage:
    python scripts/build_ratings.py
"""
from mlb_elo import db, fip
from mlb_elo.elo import EloEngine


def main() -> None:
    conn = db.connect()
    cur = conn.execute(
        """
        SELECT season, official_date, home_team_id, home_team_name,
               away_team_id, away_team_name, home_score, away_score,
               home_pitcher_id, away_pitcher_id
        FROM games
        WHERE game_type = 'R'
        ORDER BY official_date, game_pk
        """
    )
    games = cur.fetchall()
    latest_season = max(row[0] for row in games)

    engine = EloEngine()
    for (season, _date, home_id, home_name, away_id, away_name,
         home_score, away_score, home_pitcher_id, away_pitcher_id) in games:
        home_fip = away_fip = None
        if season == latest_season and home_pitcher_id and away_pitcher_id:
            home_fip = fip.fip_as_of(conn, home_pitcher_id, _date)
            away_fip = fip.fip_as_of(conn, away_pitcher_id, _date)

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
    conn.close()

    print(f"Replayed {len(games)} games.\n")
    print(f"{'Team':<26}{'Elo':>8}")
    for rank, (name, rating) in enumerate(engine.standings(), start=1):
        print(f"{rank:>2}. {name:<23}{rating:>8.1f}")


if __name__ == "__main__":
    main()
