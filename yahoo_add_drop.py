import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

import requests
from yahoo_fantasy_api import game
from yahoo_oauth import OAuth2


def ensure_oauth(oauth_path: str) -> OAuth2:
    """Load OAuth credentials, initiating auth on first run."""
    if not os.path.exists(oauth_path):
        print(
            f"oauth2 file not found at {oauth_path}. Create it with your Yahoo app credentials "
            "as shown in the README and re-run with --auth to complete login.",
            file=sys.stderr,
        )
        sys.exit(1)

    oauth = OAuth2(None, None, from_file=oauth_path)

    if not oauth.token_is_valid():
        oauth.refresh_access_token()

    return oauth


def find_league_key(oauth: OAuth2, league_id: str) -> str:
    """Find the full league key (includes game key) that ends with the provided league id."""
    gm = game.Game(oauth, "nba")
    league_keys = gm.league_ids()

    for key in league_keys:
        if key.endswith(f".l.{league_id}"):
            return key

    msg = [
        f"League id {league_id} not found on this Yahoo account.",
        "Available league keys from your account:",
        *(f"  - {lk}" for lk in league_keys),
    ]
    raise RuntimeError("\n".join(msg))


def api_get(oauth: OAuth2, url: str) -> Dict[str, Any]:
    resp = oauth.session.get(url)
    resp.raise_for_status()
    return resp.json()


def walk_get(obj: Any, key: str) -> Optional[Any]:
    """Find first value for a key in a nested Yahoo fantasy response."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = walk_get(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = walk_get(item, key)
            if found is not None:
                return found
    return None


def extract_transactions(data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield raw transaction objects from the Yahoo response."""
    fc = data.get("fantasy_content", {})
    league = fc.get("league")
    if not league or len(league) < 2:
        return []

    txs = league[1].get("transactions", {})
    count = int(txs.get("count", 0))

    extracted = []
    for i in range(count):
        tx_wrapper = txs.get(str(i))
        if not tx_wrapper:
            continue
        tx = tx_wrapper.get("transaction")
        if tx:
            extracted.append(tx)
    return extracted


def extract_players_from_transaction(tx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract player blocks from a raw transaction entry."""
    players_section = walk_get(tx, "players")
    if not players_section:
        return []

    count = int(players_section.get("count", 0))
    players = []
    for i in range(count):
        player_entry = players_section.get(str(i), {}).get("player")
        if player_entry:
            players.append(player_entry)
    return players


def extract_full_name(name_block: Any) -> Optional[str]:
    if name_block is None:
        return None
    if isinstance(name_block, dict):
        return name_block.get("full") or name_block.get("full_name")
    if isinstance(name_block, list):
        # Yahoo nests a single dict in a list frequently
        for entry in name_block:
            if isinstance(entry, dict):
                name = entry.get("full") or entry.get("full_name")
                if name:
                    return name
    return None


def extract_transaction_type(tx_data_block: Any) -> Optional[str]:
    if tx_data_block is None:
        return None
    if isinstance(tx_data_block, dict):
        return tx_data_block.get("type")
    if isinstance(tx_data_block, list):
        for entry in tx_data_block:
            if isinstance(entry, dict) and "type" in entry:
                return entry["type"]
    return None


def fetch_all_transactions(oauth: OAuth2, league_key: str, page_size: int = 25) -> List[Dict[str, Any]]:
    """Paginate through all add/drop transactions for the league."""
    start = 0
    transactions: List[Dict[str, Any]] = []

    while True:
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/league/"
            f"{league_key}/transactions;types=add,drop;start={start};count={page_size}?format=json"
        )
        data = api_get(oauth, url)
        chunk = list(extract_transactions(data))
        if not chunk:
            break
        transactions.extend(chunk)
        if len(chunk) < page_size:
            break
        start += page_size

    return transactions


def tally_adds_drops(transactions: List[Dict[str, Any]]) -> Dict[str, Counter]:
    adds = Counter()
    drops = Counter()

    for tx in transactions:
        for player in extract_players_from_transaction(tx):
            name_block = walk_get(player, "name")
            tx_data_block = walk_get(player, "transaction_data")
            name = extract_full_name(name_block)
            tx_type = extract_transaction_type(tx_data_block)

            if not name or not tx_type:
                continue

            if tx_type == "add":
                adds[name] += 1
            elif tx_type == "drop":
                drops[name] += 1

    return {"adds": adds, "drops": drops}


def print_top(counter: Counter, title: str, limit: int) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    for idx, (name, count) in enumerate(counter.most_common(limit), start=1):
        print(f"{idx:2d}. {name}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show most added and dropped players for a Yahoo Fantasy Basketball league."
    )
    parser.add_argument("--league-id", default="10145", help="Numeric Yahoo league id (no game key).")
    parser.add_argument(
        "--oauth-file",
        default="oauth2.json",
        help="Path to oauth2.json with Yahoo app credentials and tokens.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="How many players to show in each list.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="Batch size when paging through transactions (max per request).",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Force an auth refresh; useful on first run after creating oauth2.json.",
    )
    args = parser.parse_args()

    oauth = ensure_oauth(args.oauth_file)

    if args.auth:
        if not oauth.token_is_valid():
            oauth.refresh_access_token()
        print("Auth completed.")
        return

    league_key = find_league_key(oauth, args.league_id)

    print(f"Using league: {league_key}")
    transactions = fetch_all_transactions(oauth, league_key, args.page_size)
    print(f"Fetched {len(transactions)} transactions.")

    tallies = tally_adds_drops(transactions)
    print_top(tallies["adds"], "Most Added", args.top)
    print_top(tallies["drops"], "Most Dropped", args.top)


if __name__ == "__main__":
    main()
