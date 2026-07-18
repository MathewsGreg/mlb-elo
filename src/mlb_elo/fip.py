"""FIP (Fielding Independent Pitching) as a defense-independent proxy for
starting-pitcher quality, computed only from a pitcher's own strikeouts,
walks, hit-by-pitches, and home runs allowed — the outcomes a pitcher
controls without depending on their defense or sequencing luck, unlike ERA.

FIP = (13*HR + 3*(BB+HBP) - 2*K) / IP + FIP_CONSTANT

FIP_CONSTANT rescales the formula onto the same numeric scale as league ERA
(without it, FIP centers near zero). Real leaguewide constants drift year to
year with run-scoring environment; this uses a fixed, documented value
typical of the recent run environment rather than computing it from
league-wide 2026 totals, matching this project's small-model approach. If
predictions consistently skew, revisit this first.
"""
import sqlite3

FIP_CONSTANT = 3.10
MIN_OUTS_FOR_FIP = 96  # 32 IP / ~6 starts; below this the sample is too noisy to trust


def fip_as_of(conn: sqlite3.Connection, pitcher_id: int, before_date: str) -> float | None:
    """FIP computed only from starts strictly before `before_date`, so a
    prediction never sees a pitcher's future results. Returns None if the
    pitcher hasn't thrown enough innings yet this season to trust the sample."""
    row = conn.execute(
        """
        SELECT SUM(outs), SUM(walks), SUM(hit_by_pitch), SUM(home_runs), SUM(strikeouts)
        FROM pitcher_game_logs
        WHERE pitcher_id = ? AND game_date < ?
        """,
        (pitcher_id, before_date),
    ).fetchone()

    outs, walks, hbp, home_runs, strikeouts = row
    if outs is None or outs < MIN_OUTS_FOR_FIP:
        return None

    innings_pitched = outs / 3
    return (13 * home_runs + 3 * (walks + hbp) - 2 * strikeouts) / innings_pitched + FIP_CONSTANT
