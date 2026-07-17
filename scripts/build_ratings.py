"""Replay all stored games chronologically through the Elo engine and print
current ratings.

Usage:
    python scripts/build_ratings.py
"""
from mlb_elo import db
from mlb_elo.elo import EloEngine


def main() -> None:
    conn = db.connect()
    cur = conn.execute(
        """
        SELECT season, official_date, home_team_id, home_team_name,
               away_team_id, away_team_name, home_score, away_score
        FROM games
        WHERE game_type = 'R'
        ORDER BY official_date, game_pk
        """
    )
    games = cur.fetchall()
    conn.close()

    engine = EloEngine()
    for (season, _date, home_id, home_name, away_id, away_name,
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

    print(f"Replayed {len(games)} games.\n")
    print(f"{'Team':<26}{'Elo':>8}")
    for rank, (name, rating) in enumerate(engine.standings(), start=1):
        print(f"{rank:>2}. {name:<23}{rating:>8.1f}")


if __name__ == "__main__":
    main()
