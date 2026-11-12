#!/usr/bin/env python3
"""
🏆 THE PUTILLA AWARDS 🏆
Most Valuable Waiver Wire Pickups

Analyzes which waiver wire additions provided the most value
based on their statistical production after being picked up.
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from yahoo_fantasy_api import game, league
from yahoo_oauth import OAuth2


# Standard 9-cat league categories
CATEGORIES = ['FG%', 'FT%', 'PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO']
# For TO, lower is better
REVERSE_CATS = ['TO']


def ensure_oauth(oauth_path: str) -> OAuth2:
    """Load OAuth credentials."""
    if not os.path.exists(oauth_path):
        print(f"oauth2 file not found at {oauth_path}", file=sys.stderr)
        sys.exit(1)

    oauth = OAuth2(None, None, from_file=oauth_path)
    if not oauth.token_is_valid():
        oauth.refresh_access_token()
    return oauth


def find_league_key(oauth: OAuth2, league_id: str) -> str:
    """Find the full league key."""
    gm = game.Game(oauth, "nba")
    league_keys = gm.league_ids()

    for key in league_keys:
        if key.endswith(f".l.{league_id}"):
            return key

    raise RuntimeError(f"League id {league_id} not found")


def api_get(oauth: OAuth2, url: str) -> Dict[str, Any]:
    """Make API request."""
    resp = oauth.session.get(url)
    resp.raise_for_status()
    return resp.json()


def walk_get(obj: Any, key: str) -> Optional[Any]:
    """Find first value for a key in nested response."""
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


def get_league_settings(oauth: OAuth2, league_key: str) -> Dict[str, Any]:
    """Get league settings including stat categories."""
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/settings?format=json"
    return api_get(oauth, url)


def get_league_standings(oauth: OAuth2, league_key: str) -> Dict[str, Any]:
    """Get current league standings."""
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/standings?format=json"
    return api_get(oauth, url)


def get_all_teams(oauth: OAuth2, league_key: str) -> List[Dict[str, Any]]:
    """Get all teams in the league."""
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/teams?format=json"
    data = api_get(oauth, url)
    
    teams = []
    fc = data.get("fantasy_content", {})
    league_data = fc.get("league", [])
    
    if len(league_data) > 1:
        teams_data = league_data[1].get("teams", {})
        count = int(teams_data.get("count", 0))
        for i in range(count):
            team = teams_data.get(str(i), {}).get("team", [])
            if team:
                team_info = {}
                for item in team[0]:
                    if isinstance(item, dict):
                        team_info.update(item)
                teams.append(team_info)
    
    return teams


def fetch_all_transactions(oauth: OAuth2, league_key: str) -> List[Dict[str, Any]]:
    """Fetch all add/drop transactions."""
    transactions = []
    start = 0
    page_size = 50

    while True:
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/league/"
            f"{league_key}/transactions;types=add,drop;start={start};count={page_size}?format=json"
        )
        data = api_get(oauth, url)
        
        fc = data.get("fantasy_content", {})
        league_data = fc.get("league")
        if not league_data or len(league_data) < 2:
            break

        txs = league_data[1].get("transactions", {})
        count = int(txs.get("count", 0))

        chunk = []
        for i in range(count):
            tx_wrapper = txs.get(str(i))
            if tx_wrapper:
                tx = tx_wrapper.get("transaction")
                if tx:
                    chunk.append(tx)
        
        if not chunk:
            break
        transactions.extend(chunk)
        if len(chunk) < page_size:
            break
        start += page_size

    return transactions


def extract_waiver_adds(transactions: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    """Extract all waiver wire adds with team info."""
    adds_by_player = defaultdict(list)
    
    for tx in transactions:
        # Get transaction metadata
        tx_meta = tx[0] if isinstance(tx, list) else tx
        tx_id = tx_meta.get("transaction_id")
        timestamp = tx_meta.get("timestamp")
        
        # Get players section
        players_section = walk_get(tx, "players")
        if not players_section:
            continue
            
        count = int(players_section.get("count", 0))
        
        for i in range(count):
            player_entry = players_section.get(str(i), {}).get("player")
            if not player_entry:
                continue
            
            # Extract player info
            player_info = {}
            if isinstance(player_entry, list) and len(player_entry) > 0:
                for item in player_entry[0]:
                    if isinstance(item, dict):
                        if "name" in item:
                            player_info["name"] = item["name"].get("full", "Unknown")
                        if "player_key" in item:
                            player_info["player_key"] = item["player_key"]
                        if "editorial_team_abbr" in item:
                            player_info["team"] = item["editorial_team_abbr"]
                        if "display_position" in item:
                            player_info["position"] = item["display_position"]
                
                # Get transaction data
                if len(player_entry) > 1:
                    tx_data = player_entry[1].get("transaction_data", {})
                    if isinstance(tx_data, list):
                        tx_data = tx_data[0] if tx_data else {}
                    
                    action = tx_data.get("type", "")
                    source = tx_data.get("source_type", "")
                    dest_team = tx_data.get("destination_team_key", "")
                    dest_team_name = tx_data.get("destination_team_name", "")
                    
                    # Only count waiver/free agent adds
                    if action == "add" and source in ["waivers", "freeagents"]:
                        adds_by_player[player_info.get("name", "Unknown")].append({
                            "player_key": player_info.get("player_key"),
                            "team": player_info.get("team", ""),
                            "position": player_info.get("position", ""),
                            "fantasy_team": dest_team_name,
                            "fantasy_team_key": dest_team,
                            "timestamp": timestamp,
                            "source": source
                        })
    
    return adds_by_player


def get_player_stats(oauth: OAuth2, league_key: str, player_key: str) -> Dict[str, float]:
    """Get season stats for a player."""
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;player_keys={player_key}/stats?format=json"
    
    try:
        data = api_get(oauth, url)
        stats = {}
        
        # Navigate to stats
        player_stats = walk_get(data, "player_stats")
        if player_stats:
            stats_list = player_stats.get("stats", [])
            for stat in stats_list:
                stat_data = stat.get("stat", {})
                stat_id = stat_data.get("stat_id")
                value = stat_data.get("value", "0")
                
                # Map stat IDs to names (Yahoo Fantasy NBA stat IDs)
                stat_map = {
                    "5": "FG%",
                    "8": "FT%", 
                    "10": "3PTM",
                    "12": "PTS",
                    "15": "REB",
                    "16": "AST",
                    "17": "ST",
                    "18": "BLK",
                    "19": "TO",
                    "9004003": "FGM/FGA",
                    "9007006": "FTM/FTA",
                }
                
                if str(stat_id) in stat_map:
                    try:
                        stats[stat_map[str(stat_id)]] = float(value) if value != "-" else 0.0
                    except:
                        stats[stat_map[str(stat_id)]] = 0.0
        
        return stats
    except Exception as e:
        return {}


def get_free_agents_stats(oauth: OAuth2, league_key: str, count: int = 50) -> List[Dict]:
    """Get top free agents with their stats."""
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;status=FA;sort=AR;count={count}/stats?format=json"
    
    try:
        data = api_get(oauth, url)
        players = []
        
        fc = data.get("fantasy_content", {})
        league_data = fc.get("league", [])
        
        if len(league_data) > 1:
            players_data = league_data[1].get("players", {})
            player_count = int(players_data.get("count", 0))
            
            for i in range(player_count):
                player_entry = players_data.get(str(i), {}).get("player", [])
                if player_entry:
                    player_info = {"stats": {}}
                    
                    # Extract player info from first element
                    for item in player_entry[0]:
                        if isinstance(item, dict):
                            if "name" in item:
                                player_info["name"] = item["name"].get("full", "Unknown")
                            if "player_key" in item:
                                player_info["player_key"] = item["player_key"]
                            if "editorial_team_abbr" in item:
                                player_info["team"] = item["editorial_team_abbr"]
                            if "display_position" in item:
                                player_info["position"] = item["display_position"]
                    
                    # Extract stats from second element
                    if len(player_entry) > 1:
                        player_stats = player_entry[1].get("player_stats", {})
                        stats_list = player_stats.get("stats", [])
                        
                        stat_map = {
                            "5": "FG%", "8": "FT%", "10": "3PTM", "12": "PTS",
                            "15": "REB", "16": "AST", "17": "ST", "18": "BLK", "19": "TO"
                        }
                        
                        for stat in stats_list:
                            stat_data = stat.get("stat", {})
                            stat_id = str(stat_data.get("stat_id", ""))
                            value = stat_data.get("value", "0")
                            
                            if stat_id in stat_map:
                                try:
                                    player_info["stats"][stat_map[stat_id]] = float(value) if value != "-" else 0.0
                                except:
                                    player_info["stats"][stat_map[stat_id]] = 0.0
                    
                    players.append(player_info)
        
        return players
    except Exception as e:
        print(f"Error fetching free agents: {e}")
        return []


def get_league_averages(oauth: OAuth2, league_key: str) -> Dict[str, float]:
    """Calculate league average stats from all rostered players."""
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;status=T;sort=AR;count=150/stats?format=json"
    
    totals = defaultdict(list)
    
    try:
        data = api_get(oauth, url)
        fc = data.get("fantasy_content", {})
        league_data = fc.get("league", [])
        
        if len(league_data) > 1:
            players_data = league_data[1].get("players", {})
            player_count = int(players_data.get("count", 0))
            
            stat_map = {
                "5": "FG%", "8": "FT%", "10": "3PTM", "12": "PTS",
                "15": "REB", "16": "AST", "17": "ST", "18": "BLK", "19": "TO"
            }
            
            for i in range(player_count):
                player_entry = players_data.get(str(i), {}).get("player", [])
                if player_entry and len(player_entry) > 1:
                    player_stats = player_entry[1].get("player_stats", {})
                    stats_list = player_stats.get("stats", [])
                    
                    for stat in stats_list:
                        stat_data = stat.get("stat", {})
                        stat_id = str(stat_data.get("stat_id", ""))
                        value = stat_data.get("value", "0")
                        
                        if stat_id in stat_map:
                            try:
                                val = float(value) if value != "-" else None
                                if val is not None:
                                    totals[stat_map[stat_id]].append(val)
                            except:
                                pass
    except Exception as e:
        print(f"Error calculating league averages: {e}")
    
    # Calculate averages
    averages = {}
    for cat, values in totals.items():
        if values:
            averages[cat] = sum(values) / len(values)
    
    return averages


def calculate_player_value(stats: Dict[str, float], league_avg: Dict[str, float]) -> Tuple[float, Dict[str, float]]:
    """
    Calculate a player's fantasy value based on actual impact.
    
    Criteria:
    1. Must have meaningful volume (filters out low-minute players)
    2. Counting stats weighted by scarcity and impact
    3. Percentages only count if volume is sufficient
    4. Rewards well-rounded contributors
    """
    category_values = {}
    total_value = 0.0
    
    # Get key stats with defaults
    pts = stats.get('PTS', 0)
    reb = stats.get('REB', 0)
    ast = stats.get('AST', 0)
    stl = stats.get('ST', 0)
    blk = stats.get('BLK', 0)
    threes = stats.get('3PTM', 0)
    to = stats.get('TO', 0)
    fg_pct = stats.get('FG%', 0)
    ft_pct = stats.get('FT%', 0)
    
    # VOLUME CHECK: Must have at least 200 points to qualify
    # This filters out guys who barely played
    if pts < 200:
        return -999, {}  # Disqualify low-volume players
    
    # COUNTING STATS VALUE
    # These are weighted by scarcity and fantasy impact
    
    # Points - bread and butter, but common
    pts_value = pts * 0.03
    category_values['PTS'] = pts_value
    
    # Rebounds - solid value
    reb_value = reb * 0.06
    category_values['REB'] = reb_value
    
    # Assists - valuable, especially from non-guards
    ast_value = ast * 0.08
    category_values['AST'] = ast_value
    
    # Steals - RARE and valuable, premium category
    stl_value = stl * 0.35
    category_values['ST'] = stl_value
    
    # Blocks - RARE and valuable, premium category
    blk_value = blk * 0.35
    category_values['BLK'] = blk_value
    
    # 3-Pointers - valuable counting stat
    three_value = threes * 0.12
    category_values['3PTM'] = three_value
    
    # Turnovers - PENALTY (more TOs = worse)
    to_penalty = to * -0.10
    category_values['TO'] = to_penalty
    
    # PERCENTAGE BONUSES (only if volume warrants it)
    # FG% bonus - only counts if scoring enough
    if pts >= 300:  # Enough volume for FG% to matter
        if fg_pct >= 0.500:
            fg_bonus = (fg_pct - 0.450) * 50  # Bonus above 45%
        elif fg_pct >= 0.450:
            fg_bonus = (fg_pct - 0.450) * 25
        else:
            fg_bonus = (fg_pct - 0.450) * 30  # Penalty for bad FG%
        category_values['FG%'] = fg_bonus
    else:
        fg_bonus = 0
        category_values['FG%'] = 0
    
    # FT% bonus - only if taking enough FTs
    if pts >= 300:
        if ft_pct >= 0.800:
            ft_bonus = (ft_pct - 0.750) * 30
        elif ft_pct >= 0.700:
            ft_bonus = (ft_pct - 0.750) * 15
        else:
            ft_bonus = (ft_pct - 0.750) * 20  # Penalty for bad FT%
        category_values['FT%'] = ft_bonus
    else:
        ft_bonus = 0
        category_values['FT%'] = 0
    
    # TOTAL VALUE
    total_value = (pts_value + reb_value + ast_value + stl_value + 
                   blk_value + three_value + to_penalty + fg_bonus + ft_bonus)
    
    # BONUS: Well-rounded contributor bonus
    # If good in 5+ categories, add bonus
    good_cats = 0
    if pts >= 400: good_cats += 1
    if reb >= 150: good_cats += 1
    if ast >= 100: good_cats += 1
    if stl >= 30: good_cats += 1
    if blk >= 25: good_cats += 1
    if threes >= 50: good_cats += 1
    
    if good_cats >= 5:
        total_value *= 1.15  # 15% bonus for elite all-around
    elif good_cats >= 4:
        total_value *= 1.08  # 8% bonus for well-rounded
    
    return total_value, category_values


def analyze_waiver_value(oauth: OAuth2, league_key: str, waiver_adds: Dict[str, List[Dict]]) -> List[Dict]:
    """Analyze the value provided by each waiver pickup."""
    print("\n📊 Analyzing player values...")
    
    # Get league averages for comparison
    league_avg = get_league_averages(oauth, league_key)
    print(f"   Calculated league averages for {len(league_avg)} categories")
    
    player_values = []
    processed = 0
    total = len(waiver_adds)
    
    for player_name, adds in waiver_adds.items():
        if not adds:
            continue
            
        # Get the first add (when they were first picked up)
        first_add = min(adds, key=lambda x: int(x.get("timestamp", 0)))
        player_key = first_add.get("player_key")
        
        if not player_key:
            continue
        
        # Get player's current season stats
        stats = get_player_stats(oauth, league_key, player_key)
        
        if stats:
            value, cat_values = calculate_player_value(stats, league_avg)
            
            player_values.append({
                "name": player_name,
                "player_key": player_key,
                "team": first_add.get("team", ""),
                "position": first_add.get("position", ""),
                "fantasy_team": first_add.get("fantasy_team", ""),
                "pickup_count": len(adds),
                "stats": stats,
                "total_value": value,
                "category_values": cat_values,
                "first_pickup": first_add.get("timestamp")
            })
        
        processed += 1
        if processed % 10 == 0:
            print(f"   Processed {processed}/{total} players...")
    
    # Sort by total value
    player_values.sort(key=lambda x: x["total_value"], reverse=True)
    
    return player_values


def print_putilla_awards(player_values: List[Dict], top_n: int = 15):
    """Print the Putilla Awards results."""
    
    # Filter to only qualified players (positive value)
    qualified = [p for p in player_values if p['total_value'] > 0]
    
    print("\n" + "="*70)
    print("🏆" + " "*25 + "THE PUTILLA AWARDS" + " "*25 + "🏆")
    print("="*70)
    print("        Most Valuable Waiver Wire Pickups of the Season")
    print("="*70)
    
    if not qualified:
        print("\nNo waiver wire pickups found with sufficient data.")
        return
    
    # Main award - MVP Waiver Pickup
    print("\n" + "🥇 PUTILLA AWARD WINNER - MVP WAIVER WIRE PICKUP 🥇")
    print("-"*70)
    winner = qualified[0]
    print(f"\n   {winner['name']} ({winner['position']} - {winner['team']})")
    print(f"   Picked up by: {winner['fantasy_team']}")
    print(f"   Value Score: {winner['total_value']:.2f}")
    print(f"   Times Picked Up: {winner['pickup_count']}")
    print(f"\n   Season Stats:")
    for cat, val in winner['stats'].items():
        if cat in ['FG%', 'FT%']:
            print(f"      {cat}: {val:.3f}")
        elif cat in ['FGM/FGA', 'FTM/FTA']:
            continue  # Skip these
        else:
            print(f"      {cat}: {val:.0f}")
    
    # Top 12 waiver pickups
    print("\n\n" + "📋 TOP 12 WAIVER WIRE PICKUPS" + " "*30)
    print("-"*70)
    print(f"{'Rank':<5} {'Player':<22} {'Pos':<6} {'Team':<5} {'Value':>8} {'Fantasy Team':<20}")
    print("-"*70)
    
    for i, player in enumerate(qualified[:12], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        print(f"{medal}{i:<3} {player['name']:<22} {player['position']:<6} {player['team']:<5} "
              f"{player['total_value']:>8.2f} {player['fantasy_team']:<20}")
    
    # Category leaders (only from qualified players)
    print("\n\n" + "📊 CATEGORY SPECIALISTS (Min 200 PTS to qualify)" + " "*15)
    print("-"*70)
    
    cat_names = {
        "FG%": "Best FG% Pickup",
        "FT%": "Best FT% Pickup", 
        "3PTM": "3-Point Specialist",
        "PTS": "Scoring Pickup",
        "REB": "Rebounding Pickup",
        "AST": "Assist Machine",
        "ST": "Steal Artist",
        "BLK": "Shot Blocker",
        "TO": "Lowest Turnovers"
    }
    
    for cat in CATEGORIES:
        if cat == 'TO':
            # For TO, find lowest (best)
            best = min(qualified, key=lambda x: x['stats'].get(cat, 999))
        else:
            best = max(qualified, key=lambda x: x['stats'].get(cat, -999))
        
        stat_val = best['stats'].get(cat, 0)
        if cat in ['FG%', 'FT%']:
            stat_str = f"{stat_val:.3f}"
        else:
            stat_str = f"{stat_val:.0f}"
        print(f"   {cat_names.get(cat, cat):<22}: {best['name']:<20} ({stat_str})")
    
    # Team with best waiver pickups (only count qualified pickups)
    print("\n\n" + "👑 WAIVER WIRE MANAGER RANKINGS" + " "*30)
    print("-"*70)
    
    team_values = defaultdict(lambda: {"total": 0, "players": [], "count": 0})
    for player in qualified:
        team = player['fantasy_team']
        if team:
            team_values[team]["total"] += player['total_value']
            team_values[team]["players"].append((player['name'], player['total_value']))
            team_values[team]["count"] += 1
    
    sorted_teams = sorted(team_values.items(), key=lambda x: x[1]["total"], reverse=True)
    
    if sorted_teams:
        print(f"\n{'Rank':<5} {'Manager':<30} {'Value':>8} {'Pickups':>8}")
        print("-"*55)
        
        for i, (team, data) in enumerate(sorted_teams, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
            print(f"{medal}{i:<3} {team:<30} {data['total']:>8.2f} {data['count']:>8}")
        
        # Show each manager's best pickup
        print("\n\n" + "🌟 EACH MANAGER'S BEST WAIVER PICKUP" + " "*20)
        print("-"*70)
        print(f"{'Manager':<32} {'Best Pickup':<22} {'Value':>8}")
        print("-"*70)
        
        for team, data in sorted_teams:
            if data['players']:
                best = max(data['players'], key=lambda x: x[1])
                print(f"{team:<32} {best[0]:<22} {best[1]:>8.2f}")
        
        # Detailed breakdown for top manager
        print("\n\n" + "📊 DETAILED BREAKDOWN: " + sorted_teams[0][0])
        print("-"*70)
        winner_team = sorted_teams[0][0]
        winner_data = sorted_teams[0][1]
        
        print(f"\nTotal Value Added: {winner_data['total']:.2f}")
        print(f"Qualified Pickups: {winner_data['count']}")
        print(f"\nAll Qualified Pickups (sorted by value):")
        print(f"{'Rank':<5} {'Player':<25} {'Value':>10}")
        print("-"*42)
        
        sorted_players = sorted(winner_data['players'], key=lambda x: x[1], reverse=True)
        for i, (player_name, value) in enumerate(sorted_players, 1):
            print(f"{i:<5} {player_name:<25} {value:>10.2f}")
    
    print("\n" + "="*70)
    print("🏆" + " "*20 + "END OF PUTILLA AWARDS" + " "*20 + "🏆")
    print("="*70)


def main():
    parser = argparse.ArgumentParser(description="🏆 The Putilla Awards - Most Valuable Waiver Wire Pickups")
    parser.add_argument("--league-id", default="10145", help="Yahoo league ID")
    parser.add_argument("--oauth-file", default="oauth2.json", help="OAuth credentials file")
    parser.add_argument("--top", type=int, default=15, help="Number of top players to show")
    args = parser.parse_args()

    print("\n" + "="*70)
    print("🏆 THE PUTILLA AWARDS - Waiver Wire Value Analyzer 🏆")
    print("="*70)

    oauth = ensure_oauth(args.oauth_file)
    league_key = find_league_key(oauth, args.league_id)
    
    print(f"\n🏀 League: {league_key}")
    
    # Fetch all transactions
    print("\n📥 Fetching transactions...")
    transactions = fetch_all_transactions(oauth, league_key)
    print(f"   Found {len(transactions)} transactions")
    
    # Extract waiver adds
    print("\n🔍 Identifying waiver wire pickups...")
    waiver_adds = extract_waiver_adds(transactions)
    print(f"   Found {len(waiver_adds)} unique players picked up from waivers")
    
    # Analyze player values
    player_values = analyze_waiver_value(oauth, league_key, waiver_adds)
    
    # Print the awards
    print_putilla_awards(player_values, args.top)


if __name__ == "__main__":
    main()
