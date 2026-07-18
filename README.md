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

`docs/index.html` is a static, GitHub Pages–hosted dashboard: current Elo rankings
next to an ESPN RPI comparison (rank delta highlighted), and an interactive Elo
trajectory chart for the current season (pick one or two teams to trace; the rest
render as gray context).

To refresh it after pulling new results:

```
"C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe" scripts/export_dashboard.py
```

This replays `data/mlb.db` and writes `docs/data.json`, which `docs/index.html`
fetches at load time. Commit and push both files to update the published page.

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
- `src/mlb_elo/elo.py` — Elo engine (home-field edge, run-differential MOV multiplier, between-season regression); no starter or park adjustment yet
- `scripts/build_ratings.py` — replays all stored games and prints current ratings (done)
- `scripts/export_dashboard.py` — exports ratings, season history, and the ESPN RPI comparison to `docs/data.json` (done)
- `docs/index.html` — static dashboard, GitHub Pages–hosted (done)

Next: adjust for starting pitcher and ballpark, then generate next-day predictions and log them against Vegas closing lines for calibration.
