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
- `scripts/export_dashboard.py` — exports ratings, season history, ESPN RPI comparison, upcoming predictions, and the graded track record to `docs/data.json` (done)
- `docs/index.html` — static dashboard, GitHub Pages–hosted (done)

The pitcher adjustment only applies to 2026 games where both starters have
logged at least 32 innings this season (see `fip.MIN_OUTS_FOR_FIP`) — early
starts and 2023-2025 fall back to team-strength-only. `elo.PITCHER_ELO_SCALE`
(Elo points per run of FIP gap between starters) is a documented estimate,
not a backtested constant — worth revisiting if predictions skew, which the
track record building up in `predictions` should now make visible over time.

Refresh order for 2026 pitcher data: `backfill_pitchers.py` →
`fetch_pitcher_stats.py` → `export_dashboard.py`. Full daily order (including
predictions) is in the Dashboard section above.

Next: park adjustment; Vegas comparison once an API key exists; a full-history
Brier/calibration backtest (distinct from the live track record — see Roadmap
item 2 below, still not built).

## Roadmap: Vegas comparison and a full-history calibration backtest

**1. Vegas closing-line comparison (blocked on an API key)**
Needs [The Odds API](https://the-odds-api.com) (free tier, ~500 requests/month,
no credit card) — sign up, then drop the key in a new `.env` file (already
gitignored) as `ODDS_API_KEY=...`. Once that exists:
- Fetch the `h2h` (moneyline) market for upcoming MLB games.
- **Devig** the two-sided moneyline into a fair win probability — raw implied
  probability from odds always sums to >100% (the bookmaker's margin), so
  normalize the two sides to sum to 1 before treating it as a probability.
- Write it into `predictions.vegas_home_prob` (the column already exists,
  currently always `NULL`) at the same time `predict.py` logs the Elo pick,
  keyed by the same `game_pk` — `export_dashboard.py`'s scorecard and upcoming
  list already read that column, so the dashboard picks it up with no further
  changes once it's populated.
- This can only accumulate going forward (the free tier doesn't offer
  historical odds), so the earlier this starts logging, the sooner there's a
  meaningful sample to compare Brier scores head-to-head against Vegas.

**2. Full-history Brier/calibration backtest (no new script yet)**
The live track record on the dashboard only scores predictions made *going
forward* from whenever `predict.py` started running — meaningful, but small
and slow to accumulate. Separately, and with no blockers: every
historical game already has a "pregame" prediction implicit in the replay —
the `expected_home` value computed right before each rating update, built
only from information available before that game (no lookahead, by
construction). Capture that value alongside the actual outcome for all 8,735+
replayed games and score it:
- **Brier score**: `mean((predicted_prob - actual_outcome)^2)` — lower is
  better, 0.25 is what an always-50% model scores, 0 is a psychic.
- **Reliability/calibration curve**: bucket predictions (e.g. 0-10%, 10-20%,
  ... 90-100%) and check whether the actual home-win rate in each bucket
  matches the bucket's midpoint. This is the real calibration check — Brier
  score alone can hide a model that's accurate on average but miscalibrated
  in the tails (overconfident favorites, underconfident dogs, etc).
- Worth comparing pitcher-adjusted vs. team-strength-only predictions
  separately, to see whether `PITCHER_ELO_SCALE` (still an unbacktested
  estimate — see Status above) is actually helping or just adding noise.
