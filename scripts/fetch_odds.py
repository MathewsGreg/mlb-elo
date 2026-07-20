"""Pull Vegas moneyline odds for upcoming MLB games and log a devigged fair
home-win probability into predictions.vegas_home_prob, keyed by game_pk.

Devigging: a moneyline's raw implied probabilities sum to >100% (the
bookmaker's margin/vig), so each bookmaker's two-sided price is normalized to
sum to 1 before being treated as a probability. When multiple bookmakers are
returned, their devigged probabilities are averaged into one consensus number.

Only fills predictions that don't have a Vegas number yet (write-once, same
as predict.py) -- run this once a day, any time after predict.py has logged
that day's Elo picks.

Requires ODDS_API_KEY, either already in the environment or in a .env file
in the project root (KEY=VALUE per line, gitignored). Free tier is ~500
requests/month; this script costs 1 request per run regardless of how many
games are in that day's slate.

Usage:
    python scripts/fetch_odds.py
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

from mlb_elo import db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"

# MLB's season runs entirely within US Daylight Time (late March-early
# November), so a fixed UTC-4 offset recovers the correct home-city game
# date from commence_time even for the latest West Coast starts -- needed
# because the API returns every event through the next day or two, and
# team pairs that play a multi-game series would otherwise collide on a
# team-name-only match.
ET_OFFSET = timedelta(hours=-4)


def commence_date_et(commence_time: str) -> str:
    dt = datetime.strptime(commence_time, "%Y-%m-%dT%H:%M:%SZ")
    return (dt + ET_OFFSET).date().isoformat()


def load_env(env_path: Path = PROJECT_ROOT / ".env") -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def devig(outcomes: list[dict]) -> float | None:
    """outcomes: the h2h market's two-team list of {"name", "price"} (American
    odds). Returns the fair (vig-removed) probability of outcomes[0], or None
    if it isn't a clean two-sided market."""
    if len(outcomes) != 2:
        return None

    implied = []
    for outcome in outcomes:
        price = outcome["price"]
        implied.append(100 / (price + 100) if price > 0 else -price / (-price + 100))

    total = sum(implied)
    if total <= 0:
        return None
    return implied[0] / total


def fetch_events(api_key: str) -> list[dict]:
    resp = requests.get(ODDS_URL, params={
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }, timeout=30)
    resp.raise_for_status()
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        print(f"The Odds API: {remaining} requests remaining this billing period.")
    return resp.json()


def consensus_home_prob(event: dict) -> float | None:
    home_team = event["home_team"]
    fair_probs = []
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market["key"] != "h2h":
                continue
            outcomes = market["outcomes"]
            # devig() expects outcomes[0] to be the team we want the prob for
            if outcomes[0]["name"] != home_team:
                outcomes = list(reversed(outcomes))
            prob = devig(outcomes)
            if prob is not None:
                fair_probs.append(prob)
    if not fair_probs:
        return None
    return sum(fair_probs) / len(fair_probs)


def main() -> None:
    load_env()
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        sys.exit("ODDS_API_KEY not set (add it to .env as ODDS_API_KEY=... or export it).")

    conn = db.connect()
    pending = conn.execute(
        "SELECT game_pk, official_date, home_team_name, away_team_name "
        "FROM predictions WHERE vegas_home_prob IS NULL"
    ).fetchall()
    if not pending:
        print("No predictions pending a Vegas line.")
        return
    # Keyed by (date, home, away) -- a bare team-name key would collide when
    # the same two teams play a multi-game series on consecutive days, since
    # the API returns odds for every upcoming date at once, not just today.
    pending_by_matchup: dict[tuple[str, str, str], int] = {
        (official_date, home_name, away_name): game_pk
        for game_pk, official_date, home_name, away_name in pending
    }

    events = fetch_events(api_key)

    updates = []
    unmatched_events = []
    for event in events:
        key = (commence_date_et(event["commence_time"]), event["home_team"], event["away_team"])
        game_pk = pending_by_matchup.get(key)
        if game_pk is None:
            unmatched_events.append(f"{event['commence_time']}: {event['away_team']} @ {event['home_team']}")
            continue
        prob = consensus_home_prob(event)
        if prob is None:
            continue
        updates.append({"game_pk": game_pk, "vegas_home_prob": round(prob, 4)})

    db.update_vegas_probs(conn, updates)
    conn.close()

    matched_keys = {
        (commence_date_et(e["commence_time"]), e["home_team"], e["away_team"]) for e in events
    }
    still_pending = [
        f"{official_date} {away} @ {home}"
        for (official_date, home, away) in pending_by_matchup
        if (official_date, home, away) not in matched_keys
    ]

    print(f"Logged Vegas lines for {len(updates)} of {len(pending)} pending predictions.")
    if still_pending:
        print("No odds found for:", "; ".join(still_pending))
    if unmatched_events:
        print(f"{len(unmatched_events)} odds events were for dates/matchups with no pending prediction "
              "(expected -- the API returns more than just today's games).")


if __name__ == "__main__":
    main()
