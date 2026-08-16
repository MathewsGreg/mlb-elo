"""Backtest a "bet the divergence" signal: when the Elo model's home-win
probability and Vegas's devigged consensus disagree by more than some
threshold, is betting the side Elo likes better actually profitable, or is
Elo just noisier than the market and the disagreement means nothing?

This is deliberately skeptical of its own premise. The project's own
full-history Brier score (~0.245, see scripts/backtest.py) is worse than a
sharp book's (~0.20-0.21), so "Elo disagrees with Vegas" is at least as
likely to mean "Elo is wrong" as "Elo found an edge." The threshold sweep
and win-rate-when-disagreeing numbers below are how you tell those apart
empirically instead of assuming either one.

Uses:
  - predictions.home_elo_prob / vegas_home_prob for the edge itself
  - games.home_score/away_score for the actual outcome
  - odds_snapshots' raw American-odds prices (averaged across bookmakers per
    game/side) for ROI -- vegas_home_prob is a devigged *fair* probability,
    which is exactly the number a sportsbook won't pay you at; ROI has to be
    priced off the vig-inclusive number you'd actually bet against.

Caveats worth keeping in mind reading the output:
  - Small sample: this project has only been logging Vegas lines since
    2026-07-20, so every number below is one partial season, not a
    multi-year backtest -- treat it as a first read, not a settled result.
  - "Vegas line" here is whatever fetch_odds.py happened to pull that day
    (usually well before first pitch), not a true closing line -- a real
    closing-line-value comparison would need lines pulled right before each
    game, which this project doesn't do yet.
  - This is still tuned and evaluated on the same data, same one-pass-search
    caveat scripts/calibrate.py documents for its own constants.

Usage:
    python scripts/edge_analysis.py
"""
import math

from mlb_elo import db

THRESHOLDS = [0.0, 0.02, 0.03, 0.05, 0.07, 0.10, 0.15]
DECILE_COUNT = 5
STAKE = 100.0


def american_odds_profit(price: float, stake: float = STAKE) -> float:
    """Net profit (positive) or loss (negative, always -stake) from a stake
    on a single American-odds price if the bet wins. Caller handles losses."""
    return stake * (price / 100.0) if price > 0 else stake * (100.0 / abs(price))


def load_games(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.game_pk, p.official_date, p.home_team_name, p.away_team_name,
               p.home_elo_prob, p.vegas_home_prob, g.home_score, g.away_score
        FROM predictions p
        JOIN games g ON g.game_pk = p.game_pk
        WHERE g.status = 'Final' AND p.vegas_home_prob IS NOT NULL
        ORDER BY p.official_date
        """
    ).fetchall()

    price_rows = conn.execute(
        "SELECT game_pk, AVG(home_price) AS home_price, AVG(away_price) AS away_price "
        "FROM odds_snapshots GROUP BY game_pk"
    ).fetchall()
    prices = {gp: (hp, ap) for gp, hp, ap in price_rows}

    games = []
    for (game_pk, official_date, home_name, away_name, elo_prob, vegas_prob,
         home_score, away_score) in rows:
        home_win = 1 if home_score > away_score else 0
        home_price, away_price = prices.get(game_pk, (None, None))
        games.append({
            "game_pk": game_pk,
            "official_date": official_date,
            "home_team_name": home_name,
            "away_team_name": away_name,
            "elo_prob": elo_prob,
            "vegas_prob": vegas_prob,
            "home_win": home_win,
            "edge": elo_prob - vegas_prob,  # + = Elo likes home more than the market does
            "home_price": home_price,
            "away_price": away_price,
        })
    return games


def brier(rows: list[tuple[float, int]]) -> float | None:
    if not rows:
        return None
    return sum((prob - outcome) ** 2 for prob, outcome in rows) / len(rows)


def bet_outcome(game: dict) -> tuple[bool, float | None]:
    """(won, profit_if_won) for betting the side Elo likes more than Vegas
    does. profit_if_won is None if there's no priced market for that side --
    caller excludes those from ROI but can still count them in win rate."""
    bet_home = game["edge"] > 0
    won = (game["home_win"] == 1) if bet_home else (game["home_win"] == 0)
    price = game["home_price"] if bet_home else game["away_price"]
    profit_if_won = american_odds_profit(price) if price is not None else None
    return won, profit_if_won


def roi_for_subset(games: list[dict]) -> tuple[int, float | None, float | None]:
    """(n_priced, win_rate_on_priced, roi_pct) for games with a priced market."""
    priced = [g for g in games if g["home_price"] is not None and g["away_price"] is not None]
    if not priced:
        return 0, None, None
    wins = 0
    total_profit = 0.0
    for g in priced:
        won, profit_if_won = bet_outcome(g)
        if won:
            wins += 1
            total_profit += profit_if_won
        else:
            total_profit -= STAKE
    win_rate = wins / len(priced)
    roi_pct = 100 * total_profit / (STAKE * len(priced))
    return len(priced), win_rate, roi_pct


def threshold_sweep(games: list[dict]) -> None:
    print(f"{'edge >=':>8}  {'n':>5}  {'win%':>6}  {'elo brier':>10}  {'vegas brier':>12}  "
          f"{'n priced':>9}  {'win% priced':>12}  {'roi%':>7}")
    for threshold in THRESHOLDS:
        subset = [g for g in games if abs(g["edge"]) >= threshold]
        if not subset:
            continue
        wins = sum(1 for g in subset if bet_outcome(g)[0])
        win_rate = wins / len(subset)
        elo_brier = brier([(g["elo_prob"], g["home_win"]) for g in subset])
        vegas_brier = brier([(g["vegas_prob"], g["home_win"]) for g in subset])
        n_priced, win_rate_priced, roi_pct = roi_for_subset(subset)
        roi_str = f"{roi_pct:6.1f}%" if roi_pct is not None else "     n/a"
        win_priced_str = f"{win_rate_priced*100:11.1f}%" if win_rate_priced is not None else "        n/a"
        print(f"{threshold*100:7.0f}%  {len(subset):5d}  {win_rate*100:5.1f}%  "
              f"{elo_brier:10.4f}  {vegas_brier:12.4f}  {n_priced:9d}  {win_priced_str}  {roi_str}")


def decile_breakdown(games: list[dict]) -> None:
    """Bucket games by |edge| into equal-sized groups (deciles by default --
    fewer here since the sample is small) and show whether win rate/ROI on
    the divergence bet trends up with edge size or is flat/noisy."""
    ranked = sorted(games, key=lambda g: abs(g["edge"]))
    n = len(ranked)
    bucket_size = max(1, n // DECILE_COUNT)
    print(f"\n{'edge range':>16}  {'n':>5}  {'win% on bet':>12}  {'n priced':>9}  {'roi%':>7}")
    for i in range(DECILE_COUNT):
        lo = i * bucket_size
        hi = n if i == DECILE_COUNT - 1 else (i + 1) * bucket_size
        bucket = ranked[lo:hi]
        if not bucket:
            continue
        edge_lo, edge_hi = abs(bucket[0]["edge"]), abs(bucket[-1]["edge"])
        wins = sum(1 for g in bucket if bet_outcome(g)[0])
        win_rate = wins / len(bucket)
        n_priced, _, roi_pct = roi_for_subset(bucket)
        roi_str = f"{roi_pct:6.1f}%" if roi_pct is not None else "     n/a"
        print(f"{edge_lo*100:5.1f}-{edge_hi*100:4.1f}%  {len(bucket):5d}  {win_rate*100:11.1f}%  "
              f"{n_priced:9d}  {roi_str}")


def invert_matrix(a: list[list[float]]) -> list[list[float]]:
    """Gauss-Jordan inverse with partial pivoting. Small (3x3 here), pure
    Python -- this project has no numpy/scipy dependency and one 3-variable
    regression doesn't justify adding one."""
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("singular matrix -- features are collinear")
        m[col], m[pivot] = m[pivot], m[col]
        m[col] = [v / m[col][col] for v in m[col]]
        for r in range(n):
            if r != col:
                factor = m[r][col]
                m[r] = [v - factor * mv for v, mv in zip(m[r], m[col])]
    return [row[n:] for row in m]


def matvec(a: list[list[float]], v: list[float]) -> list[float]:
    return [sum(aij * vj for aij, vj in zip(row, v)) for row in a]


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def logistic_regression(
    x: list[list[float]], y: list[int], iters: int = 50, tol: float = 1e-9
) -> tuple[list[float], list[float]]:
    """Newton-Raphson (IRLS) logistic regression. Returns (coefficients,
    standard errors) -- no p-values baked in, since the caller wants to
    print those alongside labels for each feature."""
    p = len(x[0])
    beta = [0.0] * p
    cov = None
    for _ in range(iters):
        preds = [1.0 / (1.0 + math.exp(-sum(b * xj for b, xj in zip(beta, row)))) for row in x]
        grad = [sum(row[j] * (yi - pi) for row, yi, pi in zip(x, y, preds)) for j in range(p)]
        hessian = [[0.0] * p for _ in range(p)]
        for row, pi in zip(x, preds):
            w = pi * (1 - pi)
            for j in range(p):
                if row[j] == 0.0:
                    continue
                for k in range(p):
                    hessian[j][k] += w * row[j] * row[k]
        cov = invert_matrix(hessian)
        delta = matvec(cov, grad)
        beta = [b + d for b, d in zip(beta, delta)]
        if max(abs(d) for d in delta) < tol:
            break
    se = [math.sqrt(cov[i][i]) for i in range(p)]
    return beta, se


def significance_test(games: list[dict]) -> None:
    """The threshold sweep and decile breakdown above are descriptive and
    noisy at this sample size -- this is the actual test of the hypothesis:
    does the *size* of Elo's disagreement with Vegas predict the outcome
    beyond what Vegas's own probability already predicts?

    Fits logit(P(home win)) = a + b*logit(vegas_prob) + c*edge. If b ~= 1
    that just says Vegas's own probability is well-calibrated (expected,
    and not the interesting part). The load-bearing number is c: if it's
    positive and its z-score clears ~2 (roughly p < 0.05), edge carries real
    incremental information on top of the market price. If it's small or its
    confidence interval straddles zero, the threshold sweep's good-looking
    rows are more likely noise than a real effect -- exactly the ambiguity
    this dataset's size can't yet resolve on its own.
    """
    eps = 1e-6
    x, y = [], []
    for g in games:
        vp = min(max(g["vegas_prob"], eps), 1 - eps)
        vegas_logit = math.log(vp / (1 - vp))
        x.append([1.0, vegas_logit, g["edge"]])
        y.append(g["home_win"])

    beta, se = logistic_regression(x, y)
    labels = ["intercept", "vegas_logit", "edge"]
    print(f"\nLogistic regression: logit(home win) = a + b*vegas_logit + c*edge  (n={len(games)})")
    print(f"{'term':>12}  {'coef':>8}  {'se':>7}  {'z':>6}  {'p':>7}")
    for label, coef, s in zip(labels, beta, se):
        z = coef / s if s > 0 else float("nan")
        p_value = 2 * (1 - normal_cdf(abs(z)))
        print(f"{label:>12}  {coef:8.3f}  {s:7.3f}  {z:6.2f}  {p_value:7.4f}")
    edge_coef, edge_se = beta[2], se[2]
    edge_z = edge_coef / edge_se if edge_se > 0 else float("nan")
    verdict = (
        "edge's z-score clears ~2 -- some real signal beyond Vegas's own price, "
        "on this sample" if abs(edge_z) >= 1.96 else
        "edge's z-score doesn't clear ~2 -- can't yet distinguish this from noise "
        "at this sample size"
    )
    print(f"-> {verdict}")


def split_report(games: list[dict]) -> None:
    """Two ways of cutting the same divergence bet that might explain (or
    debunk) the threshold sweep's bumpiness: does it matter which side Elo
    ends up recommending (home vs away), and does it matter whether that
    side is already Vegas's favorite (Elo just wants *more* of the favorite)
    or Vegas's underdog (Elo disagrees about who should be favored at all)?
    Underdog moneyline ROI is structurally high-variance -- a couple of
    upsets swing it a lot -- so a big number in that row on its own isn't
    strong evidence without the win-rate column agreeing."""
    def row(label: str, subset: list[dict]) -> None:
        if not subset:
            print(f"  {label:<26} (no games)")
            return
        wins = sum(1 for g in subset if bet_outcome(g)[0])
        win_rate = wins / len(subset)
        n_priced, _, roi_pct = roi_for_subset(subset)
        roi_str = f"{roi_pct:6.1f}%" if roi_pct is not None else "     n/a"
        print(f"  {label:<26} n={len(subset):4d}  win%={win_rate*100:5.1f}%  "
              f"n_priced={n_priced:4d}  roi={roi_str}")

    print("\nBy which side Elo ends up recommending:")
    row("edge favors home", [g for g in games if g["edge"] > 0])
    row("edge favors away", [g for g in games if g["edge"] < 0])

    def recommended_is_vegas_dog(g: dict) -> bool:
        recommended_home = g["edge"] > 0
        vegas_favors_home = g["vegas_prob"] > 0.5
        return recommended_home != vegas_favors_home

    print("\nBy whether the recommended side is already Vegas's favorite,\n"
          "or a Vegas underdog Elo thinks is undervalued:")
    row("agrees w/ Vegas's favorite", [g for g in games if not recommended_is_vegas_dog(g)])
    row("bets the Vegas underdog", [g for g in games if recommended_is_vegas_dog(g)])

    print("  ^ control -- moneyline underdogs are known to run a bit +EV against\n"
          "    the vig on their own (favorites get overbet), so a good number\n"
          "    there isn't automatically evidence Elo is adding anything. Compare\n"
          "    against blindly betting *every* Vegas underdog, Elo opinion ignored\n"
          "    (note: bets the underdog side on ALL games, so it isn't restricted\n"
          "    to the same subset as the row above):")
    wins = 0
    n_priced = 0
    profit = 0.0
    for g in games:
        bet_home = g["vegas_prob"] <= 0.5  # home is the Vegas underdog
        won = (g["home_win"] == 1) if bet_home else (g["home_win"] == 0)
        wins += won
        price = g["home_price"] if bet_home else g["away_price"]
        if price is not None:
            n_priced += 1
            profit += american_odds_profit(price) if won else -STAKE
    roi_str = f"{100 * profit / (STAKE * n_priced):6.1f}%" if n_priced else "     n/a"
    print(f"  {'bet every Vegas underdog':<26} n={len(games):4d}  win%={100*wins/len(games):5.1f}%  "
          f"n_priced={n_priced:4d}  roi={roi_str}")


def baseline_comparison(games: list[dict]) -> None:
    """Two reference points for the sweep above: betting *every* game on
    Elo's own favorite (ignoring Vegas entirely), and betting every game on
    Vegas's favorite (ignoring Elo entirely) -- so the divergence signal's
    numbers can be read against "what do the two models do on their own"
    rather than in a vacuum."""
    def roi_for_picker(pick_home) -> tuple[float, float | None]:
        # Win rate is computed over the same priced subset ROI uses (not all
        # 311 games), so the two numbers are directly comparable to each
        # other and to the "win% priced" / "roi%" columns above.
        priced_games = []
        for g in games:
            bet_home = pick_home(g)
            won = (g["home_win"] == 1) if bet_home else (g["home_win"] == 0)
            price = g["home_price"] if bet_home else g["away_price"]
            if price is not None:
                priced_games.append((won, price))
        if not priced_games:
            return 0.0, None
        wins = sum(1 for won, _ in priced_games if won)
        win_rate = wins / len(priced_games)
        total_profit = sum(
            american_odds_profit(price) if won else -STAKE for won, price in priced_games
        )
        roi_pct = 100 * total_profit / (STAKE * len(priced_games))
        return win_rate, roi_pct

    elo_win_rate, elo_roi = roi_for_picker(lambda g: g["elo_prob"] > 0.5)
    vegas_win_rate, vegas_roi = roi_for_picker(lambda g: g["vegas_prob"] > 0.5)
    print(f"\nBaselines, all {len(games)} games, no edge filter:")
    print(f"  bet Elo's favorite every game:   win% {elo_win_rate*100:5.1f}%  "
          f"roi {elo_roi:5.1f}%" if elo_roi is not None else "  bet Elo's favorite every game: n/a")
    print(f"  bet Vegas's favorite every game: win% {vegas_win_rate*100:5.1f}%  "
          f"roi {vegas_roi:5.1f}%" if vegas_roi is not None else "  bet Vegas's favorite every game: n/a")


def main() -> None:
    conn = db.connect()
    games = load_games(conn)
    conn.close()

    if not games:
        print("No graded predictions with a Vegas line yet -- run fetch_odds.py for a while first.")
        return

    dates = sorted(g["official_date"] for g in games)
    n_priced = sum(1 for g in games if g["home_price"] is not None)
    print(f"{len(games)} graded games with a Vegas line, {dates[0]} to {dates[-1]} "
          f"({n_priced} with priced odds for ROI).\n")

    print("Divergence bet: when |Elo prob - Vegas prob| >= threshold, bet whichever side\n"
          "Elo likes more than the market does. 'elo brier'/'vegas brier' compare who's\n"
          "more often right specifically on the games where they disagree by at least that much.\n")
    threshold_sweep(games)

    print("\nSame data cut into equal-sized buckets by edge size instead of a fixed\n"
          "threshold, to see if the trend is monotonic or just noisy:")
    decile_breakdown(games)

    significance_test(games)
    split_report(games)
    baseline_comparison(games)


if __name__ == "__main__":
    main()
