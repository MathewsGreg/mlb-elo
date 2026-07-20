"""Full-history calibration backtest: replay every stored game and score the
model's pregame win probability against what actually happened.

Every historical game already has a "pregame" prediction implicit in the
replay -- expected_home, computed from ratings as they stood right before
that game's outcome updates them. No lookahead, by construction, so this
needs no live prediction log to build up first.

Reports:
  - Brier score (mean squared error of predicted prob vs actual outcome;
    0.25 = always guessing 50/50, 0 = perfect, lower is better)
  - Calibration table: bucket predictions into 10% bins and check whether
    the actual home-win rate in each bucket matches the bucket's midpoint
  - For 2026 games with both starters' FIP available: pitcher-adjusted
    Brier score vs a team-strength-only counterfactual on the same games,
    to see whether PITCHER_ELO_SCALE is actually helping

Usage:
    python scripts/backtest.py
"""
from mlb_elo import db, fip
from mlb_elo.elo import EloEngine, predict_win_prob

BUCKET_WIDTH = 0.10


def brier_score(rows: list[tuple[float, int]]) -> float:
    return sum((prob - outcome) ** 2 for prob, outcome in rows) / len(rows)


def calibration_table(rows: list[tuple[float, int]]) -> list[tuple[float, float, int, float]]:
    buckets: dict[int, list[tuple[float, int]]] = {}
    for prob, outcome in rows:
        idx = min(int(prob / BUCKET_WIDTH), int(1 / BUCKET_WIDTH) - 1)
        buckets.setdefault(idx, []).append((prob, outcome))

    table = []
    for idx in sorted(buckets):
        bucket_rows = buckets[idx]
        lo, hi = idx * BUCKET_WIDTH, (idx + 1) * BUCKET_WIDTH
        actual_rate = sum(outcome for _, outcome in bucket_rows) / len(bucket_rows)
        table.append((lo, hi, len(bucket_rows), actual_rate))
    return table


def run_replay(conn) -> dict:
    """Replay full history once using elo.py/fip.py's *current* module-level
    constants and return the raw (prediction, outcome) pairs needed to score
    them. Callers that want to try different constants (see
    scripts/calibrate.py) should monkeypatch the elo/fip modules' globals
    before calling this -- process_game, predict_win_prob, and fip_as_of all
    read their tuning constants at call time, so patching the module
    attribute is enough; no plumbing them through as arguments."""
    cur = conn.execute(
        """
        SELECT season, official_date, home_team_id, home_team_name,
               away_team_id, away_team_name, home_score, away_score,
               home_pitcher_id, away_pitcher_id
        FROM games
        WHERE game_type = 'R' AND status = 'Final'
        ORDER BY official_date, game_pk
        """
    )
    games = cur.fetchall()
    latest_season = max(row[0] for row in games)

    engine = EloEngine()
    all_predictions: list[tuple[float, int]] = []
    pitcher_subset_adjusted: list[tuple[float, int]] = []
    pitcher_subset_team_only: list[tuple[float, int]] = []

    for (season, official_date, home_id, home_name, away_id, away_name,
         home_score, away_score, home_pitcher_id, away_pitcher_id) in games:
        home_fip = away_fip = None
        if season == latest_season and home_pitcher_id and away_pitcher_id:
            home_fip = fip.fip_as_of(conn, home_pitcher_id, official_date)
            away_fip = fip.fip_as_of(conn, away_pitcher_id, official_date)

        home_rating = engine.get_rating(home_id)
        away_rating = engine.get_rating(away_id)
        actual_home = 1 if home_score > away_score else 0

        expected_home = predict_win_prob(home_rating, away_rating, home_fip, away_fip)
        all_predictions.append((expected_home, actual_home))

        if home_fip is not None and away_fip is not None:
            expected_team_only = predict_win_prob(home_rating, away_rating, None, None)
            pitcher_subset_adjusted.append((expected_home, actual_home))
            pitcher_subset_team_only.append((expected_team_only, actual_home))

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

    return {
        "latest_season": latest_season,
        "all_predictions": all_predictions,
        "pitcher_subset_adjusted": pitcher_subset_adjusted,
        "pitcher_subset_team_only": pitcher_subset_team_only,
    }


def main() -> None:
    conn = db.connect()
    result = run_replay(conn)
    conn.close()

    latest_season = result["latest_season"]
    all_predictions = result["all_predictions"]
    pitcher_subset_adjusted = result["pitcher_subset_adjusted"]
    pitcher_subset_team_only = result["pitcher_subset_team_only"]

    print(f"{len(all_predictions)} games replayed, {latest_season} is the latest season.\n")

    print(f"Overall Brier score: {brier_score(all_predictions):.4f}  (0.25 = coin flip, 0 = perfect)\n")

    print("Calibration (predicted home-win % bucket vs actual home-win rate):")
    print(f"{'bucket':>12}  {'n':>6}  {'actual rate':>12}")
    for lo, hi, n, actual_rate in calibration_table(all_predictions):
        print(f"{lo*100:5.0f}-{hi*100:3.0f}%  {n:6d}  {actual_rate*100:11.1f}%")

    if pitcher_subset_adjusted:
        print(
            f"\n{len(pitcher_subset_adjusted)} {latest_season} games had FIP for both "
            "starters -- pitcher-adjusted vs team-strength-only on that same subset:"
        )
        print(f"  pitcher-adjusted Brier:  {brier_score(pitcher_subset_adjusted):.4f}")
        print(f"  team-strength-only Brier: {brier_score(pitcher_subset_team_only):.4f}")
    else:
        print(f"\nNo {latest_season} games yet with FIP available for both starters.")


if __name__ == "__main__":
    main()
