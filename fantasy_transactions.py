#!/usr/bin/env python3
"""
Yahoo Fantasy Basketball - Enhanced Transaction Analyzer

An enhanced version that adds trending analysis and CSV export
on top of the basic add/drop tracking.

Usage:
    python fantasy_transactions.py --league-id 10145
    python fantasy_transactions.py --league-id 10145 --export
    python fantasy_transactions.py --league-id 10145 --top 30
"""

import argparse
import os
import sys
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from yahoo_fantasy_api import game
from yahoo_oauth import OAuth2

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def ensure_oauth(oauth_path: str) -> OAuth2:
    """Load OAuth credentials, initiating auth on first run."""
    if not os.path.exists(oauth_path):
        print(
            f"oauth2 file not found at {oauth_path}.\n"
            "Create it with your Yahoo app credentials:\n\n"
            '{\n'
            '    "consumer_key": "YOUR_KEY",\n'
            '    "consumer_secret": "YOUR_SECRET"\n'
            '}\n\n'
            "Get credentials at: https://developer.yahoo.com/apps/",
            file=sys.stderr,
        )
        sys.exit(1)

    oauth = OAuth2(None, None, from_file=oauth_path)

    if not oauth.token_is_valid():
        oauth.refresh_access_token()

    return oauth


def find_league_key(oauth: OAuth2, league_id: str) -> str:
    """Find the full league key that ends with the provided league id."""
    gm = game.Game(oauth, "nba")
    league_keys = gm.league_ids()

    for key in league_keys:
        if key.endswith(f".l.{league_id}"):
            return key

    msg = [
        f"League id {league_id} not found on this Yahoo account.",
        "Available league keys:",
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


def extract_transactions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
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


def print_section(title: str, width: int = 60) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def print_top(counter: Counter, title: str, limit: int) -> None:
    print_section(title)
    for idx, (name, count) in enumerate(counter.most_common(limit), start=1):
        print(f"{idx:2d}. {name}: {count}")


def analyze_trends(adds: Counter, drops: Counter, top_n: int) -> None:
    """Analyze trending players based on add/drop ratios."""
    
    # Hot players (added more than dropped)
    print_section(f"📈 HOTTEST PLAYERS (Net Adds)")
    hot_players = []
    for player in adds:
        add_count = adds[player]
        drop_count = drops.get(player, 0)
        if add_count >= 2:
            net = add_count - drop_count
            ratio = add_count / max(drop_count, 1)
            hot_players.append((player, add_count, drop_count, net, ratio))
    
    hot_players.sort(key=lambda x: (x[3], x[4]), reverse=True)
    
    if hot_players:
        print(f"{'Rank':<5} {'Player':<25} {'Adds':<6} {'Drops':<6} {'Net':<5} {'Ratio':<6}")
        print("-" * 55)
        for idx, (name, add_c, drop_c, net, ratio) in enumerate(hot_players[:top_n], 1):
            print(f"{idx:<5} {name:<25} {add_c:<6} {drop_c:<6} {net:<5} {ratio:.2f}")
    else:
        print("Not enough data")
    
    # Cold players (dropped more than added)
    print_section(f"📉 COOLING PLAYERS (Net Drops)")
    cold_players = []
    for player in drops:
        drop_count = drops[player]
        add_count = adds.get(player, 0)
        if drop_count >= 2:
            net = drop_count - add_count
            ratio = drop_count / max(add_count, 1)
            cold_players.append((player, drop_count, add_count, net, ratio))
    
    cold_players.sort(key=lambda x: (x[3], x[4]), reverse=True)
    
    if cold_players:
        print(f"{'Rank':<5} {'Player':<25} {'Drops':<6} {'Adds':<6} {'Net':<5} {'Ratio':<6}")
        print("-" * 55)
        for idx, (name, drop_c, add_c, net, ratio) in enumerate(cold_players[:top_n], 1):
            print(f"{idx:<5} {name:<25} {drop_c:<6} {add_c:<6} {net:<5} {ratio:.2f}")
    else:
        print("Not enough data")


def export_to_csv(adds: Counter, drops: Counter) -> None:
    """Export results to CSV files."""
    if not HAS_PANDAS:
        print("pandas not installed, skipping CSV export")
        return
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Combined analysis
    all_players = set(adds.keys()) | set(drops.keys())
    data = []
    for player in all_players:
        add_count = adds.get(player, 0)
        drop_count = drops.get(player, 0)
        net = add_count - drop_count
        data.append({
            "Player": player,
            "Adds": add_count,
            "Drops": drop_count,
            "Net": net,
            "Add_Drop_Ratio": round(add_count / max(drop_count, 1), 2)
        })
    
    df = pd.DataFrame(data)
    df = df.sort_values("Net", ascending=False)
    
    filename = f"fantasy_transactions_{timestamp}.csv"
    df.to_csv(filename, index=False)
    print(f"\n✅ Exported to: {filename}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze most added/dropped players in Yahoo Fantasy Basketball"
    )
    parser.add_argument(
        "--league-id",
        default="10145",
        help="Numeric Yahoo league id (default: 10145)"
    )
    parser.add_argument(
        "--oauth-file",
        default="oauth2.json",
        help="Path to oauth2.json (default: oauth2.json)"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of players to show (default: 20)"
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="API batch size (default: 50)"
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export results to CSV"
    )
    parser.add_argument(
        "--trends",
        action="store_true",
        help="Show trending player analysis"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Yahoo Fantasy Basketball - Transaction Analyzer")
    print("=" * 60)

    oauth = ensure_oauth(args.oauth_file)
    league_key = find_league_key(oauth, args.league_id)

    print(f"\n🏀 League: {league_key}")
    print("Fetching transactions...")
    
    transactions = fetch_all_transactions(oauth, league_key, args.page_size)
    print(f"📊 Found {len(transactions)} transactions")

    tallies = tally_adds_drops(transactions)
    adds = tallies["adds"]
    drops = tallies["drops"]

    print_top(adds, f"🔥 TOP {args.top} MOST ADDED", args.top)
    print_top(drops, f"💔 TOP {args.top} MOST DROPPED", args.top)

    if args.trends:
        analyze_trends(adds, drops, args.top)

    if args.export:
        export_to_csv(adds, drops)

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
