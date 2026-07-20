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

CREATE TABLE IF NOT EXISTS pitcher_game_logs (
    pitcher_id INTEGER NOT NULL,
    game_date TEXT NOT NULL,
    outs INTEGER NOT NULL,
    walks INTEGER NOT NULL,
    hit_by_pitch INTEGER NOT NULL,
    home_runs INTEGER NOT NULL,
    strikeouts INTEGER NOT NULL,
    PRIMARY KEY (pitcher_id, game_date)
);

CREATE TABLE IF NOT EXISTS predictions (
    game_pk INTEGER PRIMARY KEY,
    predicted_at TEXT NOT NULL,
    official_date TEXT NOT NULL,
    season INTEGER NOT NULL,
    home_team_id INTEGER NOT NULL,
    home_team_name TEXT NOT NULL,
    away_team_id INTEGER NOT NULL,
    away_team_name TEXT NOT NULL,
    home_elo_prob REAL NOT NULL,
    home_pitcher_fip REAL,
    away_pitcher_fip REAL,
    vegas_home_prob REAL
);
"""

GAMES_PITCHER_COLUMNS = [
    ("home_pitcher_id", "INTEGER"),
    ("home_pitcher_name", "TEXT"),
    ("away_pitcher_id", "INTEGER"),
    ("away_pitcher_name", "TEXT"),
]

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
    existing = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    for name, sql_type in GAMES_PITCHER_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE games ADD COLUMN {name} {sql_type}")
    conn.commit()
    return conn


def upsert_games(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(UPSERT_SQL, rows)
    conn.commit()


def set_probable_pitchers(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows: [{game_pk, home_pitcher_id, home_pitcher_name, away_pitcher_id, away_pitcher_name}]"""
    conn.executemany(
        """
        UPDATE games SET
            home_pitcher_id = :home_pitcher_id,
            home_pitcher_name = :home_pitcher_name,
            away_pitcher_id = :away_pitcher_id,
            away_pitcher_name = :away_pitcher_name
        WHERE game_pk = :game_pk
        """,
        rows,
    )
    conn.commit()


def insert_predictions(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Write-once: a game_pk already in predictions is left untouched, so a
    prediction always reflects what was said before the game, never hindsight."""
    conn.executemany(
        """
        INSERT OR IGNORE INTO predictions (
            game_pk, predicted_at, official_date, season,
            home_team_id, home_team_name, away_team_id, away_team_name,
            home_elo_prob, home_pitcher_fip, away_pitcher_fip, vegas_home_prob
        ) VALUES (
            :game_pk, :predicted_at, :official_date, :season,
            :home_team_id, :home_team_name, :away_team_id, :away_team_name,
            :home_elo_prob, :home_pitcher_fip, :away_pitcher_fip, :vegas_home_prob
        )
        """,
        rows,
    )
    conn.commit()


def upsert_pitcher_game_logs(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO pitcher_game_logs
            (pitcher_id, game_date, outs, walks, hit_by_pitch, home_runs, strikeouts)
        VALUES (:pitcher_id, :game_date, :outs, :walks, :hit_by_pitch, :home_runs, :strikeouts)
        ON CONFLICT(pitcher_id, game_date) DO UPDATE SET
            outs=excluded.outs, walks=excluded.walks, hit_by_pitch=excluded.hit_by_pitch,
            home_runs=excluded.home_runs, strikeouts=excluded.strikeouts
        """,
        rows,
    )
    conn.commit()
