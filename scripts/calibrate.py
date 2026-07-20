"""Grid-search the model's hand-picked constants against the full-history
backtest instead of leaving them as guesses.

HOME_ADVANTAGE, K_BASE, PITCHER_ELO_SCALE, and FIP_SHRINKAGE_IP were all
originally documented as estimates, not fits. This tries a range of values
for each -- one at a time, holding the others fixed -- and reports whichever
minimizes Brier score, using scripts/backtest.py's replay (via run_replay).

HOME_ADVANTAGE and K_BASE apply to every game, so they're scored against the
full-history Brier score. PITCHER_ELO_SCALE and FIP_SHRINKAGE_IP only affect
games where both starters have thrown at least one inning this season, so
scoring them against the full-history Brier would dilute their effect behind
~7,400 unrelated games; they're scored on that pitcher subset instead, and
searched jointly (as a small 2D grid) since they interact -- more shrinkage
means a given scale produces a smaller effective adjustment.

TOLERANCE handles a real trap this search runs into: PITCHER_ELO_SCALE and
FIP_SHRINKAGE_IP trade off almost perfectly (a bigger scale plus heavier
shrinkage can reproduce nearly the same effective adjustment as a smaller
scale plus lighter shrinkage), so the loss surface has a long, nearly flat
ridge rather than a single sharp minimum. Naively taking the single lowest
score chases that ridge out to whatever the largest tested value is, which
just means "more extreme constants that don't actually predict any better
than modest ones" -- not a real finding. Instead, among every candidate
within TOLERANCE of the best score found, this keeps the one closest to the
current default: if the data doesn't clearly prefer a different value, don't
adopt a more extreme one just because it's marginally ahead in the noise.

This is a one-pass coordinate search, not full cross-validation -- it's
tuning against the same data it's evaluated on, which risks a little
overfit, but with 8,700+ games and only four scalar constants each swept
over a coarse grid (and a tolerance band protecting against noise-chasing),
that risk is small next to the benefit of replacing a guess with a value the
data actually supports.

Usage:
    python scripts/calibrate.py
"""
import mlb_elo.elo as elo
import mlb_elo.fip as fip
from mlb_elo import db

from backtest import brier_score, run_replay

HOME_ADVANTAGE_GRID = [0.0, 8.0, 16.0, 24.0, 32.0, 40.0, 48.0, 56.0]
K_BASE_GRID = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
PITCHER_ELO_SCALE_GRID = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 65.0, 80.0, 100.0, 130.0]
FIP_SHRINKAGE_IP_GRID = [20.0, 40.0, 60.0, 90.0, 120.0, 180.0, 250.0, 350.0, 500.0]

# Brier-score band within which two constants are treated as indistinguishable.
TOLERANCE = 0.0003


def full_history_brier(conn) -> float:
    return brier_score(run_replay(conn)["all_predictions"])


def pitcher_subset_brier(conn) -> float:
    result = run_replay(conn)
    return brier_score(result["pitcher_subset_adjusted"])


def sweep(conn, label: str, attr_module, attr_name: str, grid: list[float], score_fn) -> float:
    default_value = getattr(attr_module, attr_name)
    print(f"{label} (current default {default_value}):")

    results = []
    for candidate in grid:
        setattr(attr_module, attr_name, candidate)
        score = score_fn(conn)
        results.append((score, candidate))
        print(f"  {attr_name}={candidate:>6.1f}  Brier={score:.5f}")

    best_score = min(score for score, _ in results)
    near_best = [value for score, value in results if score <= best_score + TOLERANCE]
    chosen = min(near_best, key=lambda v: abs(v - default_value))

    setattr(attr_module, attr_name, chosen)
    if len(near_best) > 1:
        print(f"  {len(near_best)} values within {TOLERANCE} of the best score "
              f"({best_score:.5f}) -- keeping {chosen} (closest to the current default)\n")
    else:
        print(f"  -> keeping {attr_name}={chosen}\n")
    return chosen


def sweep_pitcher_constants(conn) -> tuple[float, float]:
    default_scale, default_shrinkage = elo.PITCHER_ELO_SCALE, fip.FIP_SHRINKAGE_IP
    print(f"Pitcher-adjustment constants (current defaults PITCHER_ELO_SCALE={default_scale}, "
          f"FIP_SHRINKAGE_IP={default_shrinkage}):")
    print("Searching jointly since they trade off against each other:\n")

    results = []
    for scale in PITCHER_ELO_SCALE_GRID:
        for shrinkage in FIP_SHRINKAGE_IP_GRID:
            elo.PITCHER_ELO_SCALE = scale
            fip.FIP_SHRINKAGE_IP = shrinkage
            score = pitcher_subset_brier(conn)
            results.append((score, scale, shrinkage))
            print(f"  PITCHER_ELO_SCALE={scale:>5.1f}  FIP_SHRINKAGE_IP={shrinkage:>6.1f}  Brier={score:.5f}")

    best_score = min(score for score, _, _ in results)
    near_best = [(scale, shrinkage) for score, scale, shrinkage in results if score <= best_score + TOLERANCE]
    # Among near-ties, prefer the smallest scale (the least aggressive, easiest
    # to reason about adjustment) -- shrinkage is a tiebreaker after that.
    chosen_scale, chosen_shrinkage = min(near_best, key=lambda pair: (pair[0], pair[1]))

    elo.PITCHER_ELO_SCALE = chosen_scale
    fip.FIP_SHRINKAGE_IP = chosen_shrinkage
    print(f"\n  {len(near_best)} combinations within {TOLERANCE} of the best score ({best_score:.5f}) --")
    print(f"  keeping PITCHER_ELO_SCALE={chosen_scale}, FIP_SHRINKAGE_IP={chosen_shrinkage} "
          "(smallest scale among the near-ties)\n")
    return chosen_scale, chosen_shrinkage


def main() -> None:
    conn = db.connect()

    print("=== Constants that apply to every game (full-history Brier) ===\n")
    sweep(conn, "Home-field advantage", elo, "HOME_ADVANTAGE", HOME_ADVANTAGE_GRID, full_history_brier)
    sweep(conn, "Rating-update K-factor", elo, "K_BASE", K_BASE_GRID, full_history_brier)

    print("=== Pitcher-adjustment constants (Brier on the pitcher-data subset only) ===\n")
    sweep_pitcher_constants(conn)

    conn.close()

    print("=== Recommended constants ===")
    print(f"HOME_ADVANTAGE = {elo.HOME_ADVANTAGE}")
    print(f"K_BASE = {elo.K_BASE}")
    print(f"PITCHER_ELO_SCALE = {elo.PITCHER_ELO_SCALE}")
    print(f"FIP_SHRINKAGE_IP = {fip.FIP_SHRINKAGE_IP}")
    print("\nThese were only applied in-memory for this search -- edit src/mlb_elo/elo.py")
    print("and src/mlb_elo/fip.py by hand to actually adopt them.")


if __name__ == "__main__":
    main()
