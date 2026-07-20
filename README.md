# MLB Elo

A from-scratch Elo rating system for MLB teams, updated daily from live results,
used to predict the next day's games. Vegas closing lines are tracked separately
as a calibration benchmark, not fed into the model.

## Layout

- `src/` — Elo engine, data models, MLB API client (library code)
- `scripts/` — runnable entry points (fetch data, update ratings, predict, compare to odds)
- `data/` — local SQLite DB and any cached pulls (gitignored)
- `tests/` — unit tests

## Data sources

- [MLB Stats API](https://statsapi.mlb.com) — schedule, results, probable pitchers. Free, no key.
- [The Odds API](https://the-odds-api.com) — Vegas lines for calibration. Free tier, requires an API key.

## Setup

The virtual environment lives **outside** this Dropbox-synced folder, at
`C:\Users\Diggs\venvs\mlb_elo`, to avoid Dropbox locking files mid-write during
`pip install`. VS Code is pointed at it via `.vscode/settings.json`.

To recreate it from scratch:

```
"C:\Users\Diggs\AppData\Local\Programs\Python\Python312\python.exe" -m venv "C:\Users\Diggs\venvs\mlb_elo"
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" -m pip install -r requirements.txt
```

## Dashboard

`docs/index.html` is a static, GitHub Pages–hosted dashboard, live at
https://mathewsgreg.github.io/mlb-elo/:

- **Upcoming games & predictions** — every game logged by `predict.py`
  that hasn't been played yet, with the model's picked winner and win
  probability (Vegas column present but empty until odds are wired up)
- **Track record** — Brier score and straight-up record (e.g. "17-13") on
  every graded prediction, plus a recent-picks table marked correct/incorrect
- Current Elo rankings next to an ESPN RPI comparison (rank delta highlighted)
- An interactive Elo trajectory chart for the current season (pick one or two
  teams to trace; the rest render as gray context)
- A runs-scored-vs-allowed-per-9 scatter, positioned by actual season output
  and colored independently by Elo rating — a color/position mismatch (e.g. a
  below-average-Elo team sitting in good run-differential territory) flags
  where the model and raw run output disagree

**Daily refresh** (do this before that day's games start, so predictions stay
genuinely pregame):

```
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" scripts/fetch_history.py 2026
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" scripts/backfill_pitchers.py
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" scripts/fetch_pitcher_stats.py
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" scripts/predict.py
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" scripts/fetch_odds.py
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" scripts/export_dashboard.py
```

`predict.py` logs a prediction for every not-yet-started game on a given date
(today by default; pass `YYYY-MM-DD` for another date) and **never overwrites
an existing one** — a prediction always reflects what the model said before
the game, so the track record can't quietly become hindsight. `export_dashboard.py`
just reads what's already logged; it doesn't create predictions itself.

`export_dashboard.py` replays `data/mlb.db` and writes `docs/data.json`, which
`docs/index.html` fetches at load time. Commit and push both files (and
`data/mlb.db`'s new `predictions` table only lives locally — the JSON export
is what actually ships) to update the published page.

`docs/espn_rpi.json` is a manually captured snapshot of ESPN's MLB RPI table
(https://www.espn.com/mlb/stats/rpi/_/sort/sos) — there's no public API for it, so
refreshing the comparison means copying the current table into that file by hand
(team, rpi, wins, losses) and re-running the export script.

To preview locally before pushing (fetch() needs an HTTP server, not a `file://` URL):

```
cd docs
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" -m http.server 8791
```

then open http://localhost:8791/.

## Status

- `scripts/fetch_schedule.py` — pulls a day's schedule + probable pitchers (smoke test, done)
- `scripts/fetch_history.py` — pulls regular-season results by year into `data/mlb.db` (done; run for 2023-2026)
- `src/mlb_elo/elo.py` — Elo engine (home-field edge, run-differential MOV multiplier, between-season regression, 2026 starting-pitcher adjustment); no park adjustment yet
- `src/mlb_elo/fip.py` — FIP (defense-independent pitcher quality) as of a given date, from `pitcher_game_logs`
- `scripts/backfill_pitchers.py` — fills in `home_pitcher_id`/`away_pitcher_id` on 2026 games (done; re-run periodically to catch newer games)
- `scripts/fetch_pitcher_stats.py` — pulls per-start game logs for every 2026 starter (done; re-run periodically to keep FIP current)
- `scripts/build_ratings.py` — replays all stored games and prints current ratings (done)
- `scripts/predict.py` — logs a win-probability prediction for each not-yet-started game on a date, write-once (done)
- `scripts/backtest.py` — full-history Brier score + calibration table from the replay's implicit pregame predictions, plus pitcher-adjusted vs. team-strength-only on the current season's subset (done)
- `scripts/calibrate.py` — grid-searches `HOME_ADVANTAGE`, `K_BASE`, `PITCHER_ELO_SCALE`, and `fip.FIP_SHRINKAGE_IP` against the backtest instead of leaving them as hand-picked guesses (done)
- `scripts/fetch_odds.py` — pulls devigged Vegas moneyline probabilities from The Odds API into `predictions.vegas_home_prob`, write-once, ~1 request/day (done; needs `ODDS_API_KEY` in `.env`)
- `scripts/export_dashboard.py` — exports ratings, season history, ESPN RPI comparison, upcoming predictions, and the graded track record to `docs/data.json` (done)
- `docs/index.html` — static dashboard, GitHub Pages–hosted (done)

The pitcher adjustment applies to any 2026 game where both starters have
thrown at least one logged inning this season; earlier seasons fall back to
team-strength-only. Rather than gating on a fixed innings cutoff, a
pitcher's FIP is shrunk toward `fip.LEAGUE_AVG_FIP` proportional to how few
innings back it (`fip.FIP_SHRINKAGE_IP`) — a pitcher with only a handful of
starts sits close to league average, one with a full season's innings keeps
almost all of his own number. `elo.PITCHER_ELO_SCALE` and
`fip.FIP_SHRINKAGE_IP` were grid-searched jointly against the backtest by
`scripts/calibrate.py`, not hand-picked — see Roadmap item 2 below for what
that search found and why the original guess (no shrinkage, `PITCHER_ELO_SCALE
= 25`) was actually hurting predictions.

Refresh order for 2026 pitcher data: `backfill_pitchers.py` →
`fetch_pitcher_stats.py` → `export_dashboard.py`. Full daily order (including
predictions) is in the Dashboard section above.

Next: park adjustment; accumulate enough live Vegas lines for a head-to-head
Brier comparison; a recency-weighted (rather than full-season) pitcher
sample now that the shrinkage is in place.

## Roadmap: Vegas comparison and a full-history calibration backtest

**1. Vegas closing-line comparison (done; needs your own API key to run)**
Sign up at [The Odds API](https://the-odds-api.com) (free tier, ~500
requests/month, no credit card — costs 1 request per `fetch_odds.py` run,
regardless of how many games are in that day's slate), then drop the key in
a new `.env` file (already gitignored) in the project root as
`ODDS_API_KEY=...`. `scripts/fetch_odds.py` then:
- Fetches the `h2h` (moneyline) market for upcoming MLB games.
- **Devigs** each bookmaker's two-sided price into a fair win probability
  (raw implied probability sums to >100% because of the bookmaker's margin)
  and averages across whichever bookmakers the free tier returns.
- Writes it into `predictions.vegas_home_prob`, write-once like `predict.py`,
  matched to the same game by home/away team name. `export_dashboard.py`'s
  scorecard and upcoming list already read that column, so the dashboard
  picks it up automatically once it's populated.
- This can only accumulate going forward (the free tier doesn't offer
  historical odds), so the earlier this starts logging, the sooner there's a
  meaningful sample to compare Brier scores head-to-head against Vegas.

**2. Full-history Brier/calibration backtest (done — `scripts/backtest.py`)**
The live track record on the dashboard only scores predictions made *going
forward* from whenever `predict.py` started running — meaningful, but small
and slow to accumulate. `scripts/backtest.py` instead scores every
historical game's implicit pregame prediction — the `expected_home` value
computed right before each rating update, built only from information
available before that game (no lookahead, by construction):
- **Brier score**: `mean((predicted_prob - actual_outcome)^2)` — lower is
  better, 0.25 is what an always-50% model scores, 0 is a psychic. Current
  full-history result: **0.2446** across 8,766 games — in the right range for
  MLB (538's Elo has historically run ~0.23-0.24; a sharp Vegas book is
  typically ~0.20-0.21) but with real room to close the gap.
- **Calibration table**: buckets predictions into 10% bins and checks
  whether the actual home-win rate in each bucket matches the bucket's
  midpoint — this looks reasonably well-calibrated (e.g. 60-70% predicted →
  61.3% actual), so the model isn't systematically over/underconfident, it's
  just not very sharp yet.
- **Pitcher-adjusted vs. team-strength-only**, compared on the same subset of
  2026 games with FIP for both starters. The *first* version of this
  comparison (flat `PITCHER_ELO_SCALE = 25`, no shrinkage, gated on a hard
  32-inning cutoff) found the pitcher adjustment scoring *worse*
  (0.2523) than team-strength-only (0.2510) on those same 618 games — a raw
  FIP is dominated by home-run rate, which is noisy over the small samples
  (often 6-10 starts) the cutoff let through, and the adjustment's average
  swing (~27 Elo points) was nearly as large as the average team-strength
  gap it was added to (~45 points): a volatile stat given as much weight as
  the far more reliable team rating.

**3. Shrinkage + calibration (done — `fip.py` shrinkage, `scripts/calibrate.py`)**
Fixing item 2's finding took two changes:
- `fip.fip_as_of` now shrinks a pitcher's FIP toward `LEAGUE_AVG_FIP`,
  weighted by innings pitched against `FIP_SHRINKAGE_IP` — a few starts sits
  close to league average, a full season keeps almost all of a pitcher's own
  number, and the transition is continuous instead of a cliff at a fixed
  innings cutoff.
- `scripts/calibrate.py` grid-searches `HOME_ADVANTAGE`, `K_BASE`,
  `PITCHER_ELO_SCALE`, and `FIP_SHRINKAGE_IP` against the backtest rather
  than leaving them as guesses. `HOME_ADVANTAGE` (24) and `K_BASE` (4) both
  turned out to already be close to optimal. `PITCHER_ELO_SCALE` and
  `FIP_SHRINKAGE_IP` trade off against each other almost perfectly (a bigger
  scale plus heavier shrinkage can reproduce nearly the same effective
  adjustment as a smaller scale plus lighter shrinkage), so the search
  keeps whichever value is within a small tolerance of the best score *and*
  closest to the current default/smallest in magnitude — otherwise a plain
  grid search chases that flat ridge out to whatever the most extreme
  tested value is, which isn't a real finding, just noise. That search
  landed on `PITCHER_ELO_SCALE = 10` (down from 25) and `FIP_SHRINKAGE_IP =
  20`. With both changes, the same pitcher-vs-team-only comparison now
  reads **0.2487 vs. 0.2489** on the (now much larger, since there's no hard
  cutoff) 1,349-game subset — a small, believable edge instead of a
  measurable loss.
