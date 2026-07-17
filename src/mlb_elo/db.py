"""SQLite storage for pulled MLB game data."""
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "mlb.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_pk INTEGER PRIMARY KEY,
    official_date TEXT NOT NULL,
    game_datetime TEXT NOT NULL,
    season INTEGER NOT NULL,
    game_type TEXT NOT NULL,
    away_team_id INTEGER NOT NULL,
    away_team_name TEXT NOT NULL,
    home_team_id INTEGER NOT NULL,
    home_team_name TEXT NOT NULL,
    away_score INTEGER,
    home_score INTEGER,
    home_win INTEGER,
    venue_id INTEGER,
    venue_name TEXT,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_games_season_date
    ON games (season, official_date);
"""

UPSERT_SQL = """
INSERT INTO games (
    game_pk, official_date, game_datetime, season, game_type,
    away_team_id, away_team_name, home_team_id, home_team_name,
    away_score, home_score, home_win, venue_id, venue_name, status
) VALUES (
    :game_pk, :official_date, :game_datetime, :season, :game_type,
    :away_team_id, :away_team_name, :home_team_id, :home_team_name,
    :away_score, :home_score, :home_win, :venue_id, :venue_name, :status
)
ON CONFLICT(game_pk) DO UPDATE SET
    away_score=excluded.away_score,
    home_score=excluded.home_score,
    home_win=excluded.home_win,
    status=excluded.status;
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def upsert_games(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(UPSERT_SQL, rows)
    conn.commit()
