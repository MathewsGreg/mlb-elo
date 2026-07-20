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

A raw FIP is noisy in a small sample -- it's dominated by home-run rate,
which swings wildly over a handful of starts. Rather than gating on a fixed
innings cutoff (using a pitcher's number once he clears it, ignoring the
cutoff entirely otherwise), fip_as_of blends the raw FIP toward
LEAGUE_AVG_FIP, weighted by innings pitched against FIP_SHRINKAGE_IP (a
"pseudo-innings" prior strength) -- a pitcher with only a few innings logged
sits close to league average, one with a full season's innings keeps almost
all of his own number, and the transition is continuous rather than a cliff
at some arbitrary innings threshold.
"""
import sqlite3

FIP_CONSTANT = 3.10
LEAGUE_AVG_FIP = 4.00  # typical modern-era MLB league-average FIP; the shrinkage target
FIP_SHRINKAGE_IP = 20.0  # pseudo-innings of league-average performance blended in; grid-searched jointly with elo.PITCHER_ELO_SCALE via scripts/calibrate.py


def fip_as_of(conn: sqlite3.Connection, pitcher_id: int, before_date: str) -> float | None:
    """Shrunk FIP computed only from starts strictly before `before_date`, so
    a prediction never sees a pitcher's future results. Returns None only if
    the pitcher hasn't thrown a single logged inning yet this season -- there's
    nothing to shrink from zero."""
    row = conn.execute(
        """
        SELECT SUM(outs), SUM(walks), SUM(hit_by_pitch), SUM(home_runs), SUM(strikeouts)
        FROM pitcher_game_logs
        WHERE pitcher_id = ? AND game_date < ?
        """,
        (pitcher_id, before_date),
    ).fetchone()

    outs, walks, hbp, home_runs, strikeouts = row
    if not outs:
        return None

    innings_pitched = outs / 3
    raw_fip = (13 * home_runs + 3 * (walks + hbp) - 2 * strikeouts) / innings_pitched + FIP_CONSTANT

    weight = innings_pitched / (innings_pitched + FIP_SHRINKAGE_IP)
    return weight * raw_fip + (1 - weight) * LEAGUE_AVG_FIP
