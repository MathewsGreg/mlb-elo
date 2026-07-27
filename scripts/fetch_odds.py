"""Pull Vegas moneyline odds for upcoming MLB games. Logs the raw per-
bookmaker American-odds prices (home_price/away_price, vig included) into
odds_snapshots, and a devigged fair home-win probability into
predictions.vegas_home_prob, both keyed by game_pk.

Devigging: a moneyline's raw implied probabilities sum to >100% (the
bookmaker's margin/vig), so each bookmaker's two-sided price is normalized to
sum to 1 before being treated as a probability. When multiple bookmakers are
returned, their devigged probabilities are averaged into one consensus
number for vegas_home_prob -- but the raw priced odds are kept in
odds_snapshots too, since answering "can this model beat the spread" (as
opposed to just "is it better calibrated than the fair-value consensus")
needs the actual vig-inclusive prices a bet would be placed against.

Only fills predictions that don't have a Vegas number yet (write-once, same
as predict.py) -- run this once a day, any time after predict.py has logged
that day's Elo picks. odds_snapshots rows are append-only and keyed on
(game_pk, bookmaker_key, fetched_at), so they're unaffected by that.

Requires ODDS_API_KEY, either already in the environment or in a .env file
in the project root (KEY=VALUE per line, gitignored). Free tier is ~500
requests/month; this script costs 1 request per run regardless of how many
games are in that day's slate.

Usage:
    python scripts/fetch_odds.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
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


def implied_prob(price: int) -> float:
    """Raw (vig-included) implied probability of a single American-odds price."""
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def devig_pair(home_price: int, away_price: int) -> float | None:
    """Fair (vig-removed) home-win probability from a two-sided American-odds
    price. The two raw implied probabilities sum to >100% -- that excess is
    the bookmaker's margin -- so this normalizes them to sum to 1."""
    home_implied, away_implied = implied_prob(home_price), implied_prob(away_price)
    total = home_implied + away_implied
    if total <= 0:
        return None
    return home_implied / total


def bookmaker_prices(event: dict) -> list[dict]:
    """Raw two-sided moneyline price for each bookmaker in event, oriented so
    home_price/away_price always refer to the game's home team regardless of
    the order the API lists outcomes in. Skips any market that isn't a clean
    two-sided h2h line."""
    home_team = event["home_team"]
    away_team = event["away_team"]
    prices = []
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market["key"] != "h2h":
                continue
            outcomes = market["outcomes"]
            if len(outcomes) != 2:
                continue
            by_name = {o["name"]: o["price"] for o in outcomes}
            if home_team not in by_name or away_team not in by_name:
                continue
            prices.append({
                "bookmaker_key": bookmaker["key"],
                "bookmaker_title": bookmaker.get("title", bookmaker["key"]),
                "home_price": by_name[home_team],
                "away_price": by_name[away_team],
            })
    return prices


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


def consensus_home_prob(prices: list[dict]) -> float | None:
    """Average of each bookmaker's own devigged fair home-win probability."""
    fair_probs = [
        prob for prob in (devig_pair(p["home_price"], p["away_price"]) for p in prices)
        if prob is not None
    ]
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
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    updates = []
    snapshots = []
    unmatched_events = []
    for event in events:
        key = (commence_date_et(event["commence_time"]), event["home_team"], event["away_team"])
        game_pk = pending_by_matchup.get(key)
        if game_pk is None:
            unmatched_events.append(f"{event['commence_time']}: {event['away_team']} @ {event['home_team']}")
            continue

        prices = bookmaker_prices(event)
        for price in prices:
            snapshots.append({"game_pk": game_pk, "fetched_at": fetched_at, **price})

        prob = consensus_home_prob(prices)
        if prob is None:
            continue
        updates.append({"game_pk": game_pk, "vegas_home_prob": round(prob, 4)})

    db.insert_odds_snapshots(conn, snapshots)
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
