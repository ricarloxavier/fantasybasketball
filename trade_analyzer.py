#!/usr/bin/env python3
"""
🔄 FANTASY BASKETBALL TRADE ANALYZER 🔄

Analyzes trade proposals considering:
- Season-long performance (full body of work)
- Recent performance (last 7-14 days - hot/cold streaks)
- Injury status and history
- Team context (games per week, playoff schedule)
- Category impact (which cats you win/lose)
- Rest-of-season outlook
"""

import os
import sys
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional

from yahoo_fantasy_api import game, League
from yahoo_oauth import OAuth2


# ============================================================================
# CONFIGURATION
# ============================================================================

LEAGUE_KEY = '466.l.10145'
MY_TEAM_KEY = '466.l.10145.t.12'

# Stat ID mapping
STAT_MAP = {
    '5': 'FG%', '8': 'FT%', '10': '3PTM', '12': 'PTS',
    '15': 'REB', '16': 'AST', '17': 'ST', '18': 'BLK', '19': 'TO'
}

# Categories for analysis
CATEGORIES = ['FG%', 'FT%', '3PTM', 'PTS', 'REB', 'AST', 'ST', 'BLK', 'TO']

# Punt categories (adjust based on your build)
PUNT_CATEGORIES = ['TO']  # You're punting turnovers

# Season context
GAMES_PLAYED_SO_FAR = 35  # Approximate games played in season
GAMES_REMAINING = 47  # Approximate remaining regular season games

# Recent stats weighting (0.0 = all season, 1.0 = all recent)
RECENT_WEIGHT = 0.4  # 40% recent, 60% season for trade analysis

# XML namespace
YAHOO_NS = {'yh': 'http://fantasysports.yahooapis.com/fantasy/v2/base.rng'}


# ============================================================================
# OAUTH & UTILITIES
# ============================================================================

def ensure_oauth(oauth_path: str = 'oauth2.json') -> OAuth2:
    """Load OAuth credentials."""
    if not os.path.exists(oauth_path):
        print(f"❌ oauth2.json not found at {oauth_path}", file=sys.stderr)
        sys.exit(1)
    
    oauth = OAuth2(None, None, from_file=oauth_path)
    if not oauth.token_is_valid():
        oauth.refresh_access_token()
    return oauth


def safe_float(val: Any) -> float:
    """Convert value to float, handling '-', empty, and None."""
    if val is None or val == '-' or val == '':
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ============================================================================
# PLAYER DATA FETCHING
# ============================================================================

def get_player_stats_by_type(oauth: OAuth2, player_name: str, stat_type: str = 'season') -> Optional[Dict]:
    """
    Get player stats for a specific time period.
    
    Args:
        oauth: OAuth2 credentials
        player_name: Player name to search for
        stat_type: 'season', 'lastweek', 'lastmonth', 'average_season' (per-game)
    
    Returns:
        Dict with player info and stats, or None if not found
    """
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}/players;search={player_name}/stats;type={stat_type}"
    headers = {'Authorization': f'Bearer {oauth.access_token}'}
    
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        
        root = ET.fromstring(resp.content)
        player = root.find('.//yh:player', YAHOO_NS)
        
        if player is None:
            return None
        
        # Extract player info
        name_el = player.find('.//yh:name/yh:full', YAHOO_NS)
        team_el = player.find('.//yh:editorial_team_abbr', YAHOO_NS)
        pos_el = player.find('.//yh:display_position', YAHOO_NS)
        status_el = player.find('.//yh:status', YAHOO_NS)
        player_id_el = player.find('.//yh:player_id', YAHOO_NS)
        
        # Extract stats
        stats = {}
        for stat in player.findall('.//yh:stat', YAHOO_NS):
            stat_id = stat.find('yh:stat_id', YAHOO_NS)
            value = stat.find('yh:value', YAHOO_NS)
            if stat_id is not None and value is not None:
                stats[stat_id.text] = value.text
        
        return {
            'name': name_el.text if name_el is not None else player_name,
            'player_id': player_id_el.text if player_id_el is not None else '',
            'team': team_el.text if team_el is not None else '',
            'position': pos_el.text if pos_el is not None else '',
            'status': status_el.text if status_el is not None else '',
            'stats': stats
        }
    except Exception as e:
        print(f"Error fetching stats for {player_name}: {e}")
        return None


def get_full_player_profile(oauth: OAuth2, player_name: str) -> Optional[Dict]:
    """
    Get complete player profile with season, recent, and trend data.
    
    Returns:
        Dict with all relevant player information for trade analysis
    """
    # Fetch different time periods
    season = get_player_stats_by_type(oauth, player_name, 'season')
    recent_week = get_player_stats_by_type(oauth, player_name, 'lastweek')
    recent_month = get_player_stats_by_type(oauth, player_name, 'lastmonth')
    
    if not season:
        return None
    
    # Parse season stats (totals)
    s = season['stats']
    season_stats = {
        'PTS': safe_float(s.get('12', 0)),
        'REB': safe_float(s.get('15', 0)),
        'AST': safe_float(s.get('16', 0)),
        'ST': safe_float(s.get('17', 0)),
        'BLK': safe_float(s.get('18', 0)),
        '3PTM': safe_float(s.get('10', 0)),
        'TO': safe_float(s.get('19', 0)),
        'FG%': safe_float(s.get('5', 0)),
        'FT%': safe_float(s.get('8', 0)),
    }
    
    # Season per-game
    season_per_game = {
        cat: val / GAMES_PLAYED_SO_FAR if GAMES_PLAYED_SO_FAR > 0 else 0
        for cat, val in season_stats.items()
        if cat not in ['FG%', 'FT%']
    }
    season_per_game['FG%'] = season_stats['FG%']
    season_per_game['FT%'] = season_stats['FT%']
    
    # Recent stats
    recent_stats = {}
    recent_per_game = {}
    trend = 'STEADY ➡️'
    trend_multiplier = 1.0
    
    if recent_week and recent_week['stats']:
        r = recent_week['stats']
        recent_stats = {
            'PTS': safe_float(r.get('12', 0)),
            'REB': safe_float(r.get('15', 0)),
            'AST': safe_float(r.get('16', 0)),
            'ST': safe_float(r.get('17', 0)),
            'BLK': safe_float(r.get('18', 0)),
            '3PTM': safe_float(r.get('10', 0)),
            'TO': safe_float(r.get('19', 0)),
            'FG%': safe_float(r.get('5', 0)),
            'FT%': safe_float(r.get('8', 0)),
        }
        
        # Recent per-game (assume ~3 games in last week)
        recent_games = 3
        recent_per_game = {
            cat: val / recent_games if recent_games > 0 else 0
            for cat, val in recent_stats.items()
            if cat not in ['FG%', 'FT%']
        }
        recent_per_game['FG%'] = recent_stats['FG%']
        recent_per_game['FT%'] = recent_stats['FT%']
        
        # Calculate trend
        if season_per_game['PTS'] > 0:
            pts_ratio = recent_per_game['PTS'] / season_per_game['PTS']
            reb_ratio = recent_per_game['REB'] / season_per_game['REB'] if season_per_game['REB'] > 0 else 1.0
            ast_ratio = recent_per_game['AST'] / season_per_game['AST'] if season_per_game['AST'] > 0 else 1.0
            
            avg_ratio = (pts_ratio + reb_ratio + ast_ratio) / 3
            
            if avg_ratio >= 1.3:
                trend = 'HOT 🔥'
                trend_multiplier = 1.15
            elif avg_ratio >= 1.1:
                trend = 'WARM ↗️'
                trend_multiplier = 1.05
            elif avg_ratio <= 0.7:
                trend = 'COLD ❄️'
                trend_multiplier = 0.85
            elif avg_ratio <= 0.9:
                trend = 'COOL ↘️'
                trend_multiplier = 0.95
    
    # Projected rest-of-season stats (weighted average)
    ros_per_game = {}
    for cat in season_per_game.keys():
        if recent_per_game:
            # Weight recent performance
            ros_per_game[cat] = (recent_per_game.get(cat, 0) * RECENT_WEIGHT + 
                                season_per_game[cat] * (1 - RECENT_WEIGHT))
        else:
            ros_per_game[cat] = season_per_game[cat]
    
    # Injury analysis
    status = season['status']
    injury_risk = 'LOW'
    
    if status in ['INJ', 'O', 'IR']:
        injury_risk = 'HIGH - Currently Injured'
    elif status == 'GTD':
        injury_risk = 'MEDIUM - Game Time Decision'
    elif status == 'DTD':
        injury_risk = 'MEDIUM - Day to Day'
    
    return {
        'name': season['name'],
        'player_id': season['player_id'],
        'team': season['team'],
        'position': season['position'],
        'status': status,
        'injury_risk': injury_risk,
        'season_totals': season_stats,
        'season_per_game': season_per_game,
        'recent_stats': recent_stats,
        'recent_per_game': recent_per_game,
        'ros_per_game': ros_per_game,  # Rest of Season projection
        'trend': trend,
        'trend_multiplier': trend_multiplier,
    }


def get_team_name(oauth: OAuth2, team_key: str) -> str:
    """Get team name from team key."""
    try:
        lg = League(oauth, LEAGUE_KEY)
        teams = lg.teams()
        return teams.get(team_key, {}).get('name', team_key)
    except:
        return team_key


# ============================================================================
# TRADE ANALYSIS
# ============================================================================

def calculate_category_impact(give_players: List[Dict], get_players: List[Dict], 
                              punt_cats: List[str] = None) -> Dict:
    """
    Calculate the net category impact of a trade.
    
    Returns dict with:
    - net_change: Dict of category -> net change in per-game stats
    - winners: List of categories you improve
    - losers: List of categories you hurt
    """
    punt_cats = punt_cats or []
    
    # Sum up what you're giving
    give_totals = {cat: 0.0 for cat in CATEGORIES}
    for player in give_players:
        ros = player['ros_per_game']
        for cat in CATEGORIES:
            give_totals[cat] += ros.get(cat, 0)
    
    # Sum up what you're getting
    get_totals = {cat: 0.0 for cat in CATEGORIES}
    for player in get_players:
        ros = player['ros_per_game']
        for cat in CATEGORIES:
            get_totals[cat] += ros.get(cat, 0)
    
    # Calculate net change
    net_change = {}
    winners = []
    losers = []
    neutral = []
    
    for cat in CATEGORIES:
        if cat in punt_cats:
            net_change[cat] = 0  # Don't count punt categories
            continue
        
        # For TO, less is better
        if cat == 'TO':
            change = give_totals[cat] - get_totals[cat]  # Positive if you reduce TOs
        else:
            change = get_totals[cat] - give_totals[cat]  # Positive if you gain
        
        net_change[cat] = change
        
        # Determine if winner/loser (>5% change is significant)
        if cat in ['FG%', 'FT%']:
            threshold = 0.01  # 1% change
        else:
            base = give_totals[cat] if give_totals[cat] > 0 else 1.0
            threshold = base * 0.05  # 5% change
        
        if abs(change) < threshold:
            neutral.append(cat)
        elif change > 0:
            winners.append(cat)
        else:
            losers.append(cat)
    
    return {
        'net_change': net_change,
        'give_totals': give_totals,
        'get_totals': get_totals,
        'winners': winners,
        'losers': losers,
        'neutral': neutral,
    }


def evaluate_trade_value(give_players: List[Dict], get_players: List[Dict]) -> Dict:
    """
    Evaluate overall trade value using multiple metrics.
    
    Returns:
    - total_value_score: Weighted sum of all categories
    - ros_value_give: Total value you're giving up
    - ros_value_get: Total value you're getting
    - value_diff: Net value (positive = you win, negative = you lose)
    """
    # Category weights (adjust based on importance)
    weights = {
        'PTS': 1.0,
        'REB': 1.2,
        'AST': 1.5,  # High priority (you're defending AST)
        'ST': 1.3,
        'BLK': 1.3,
        '3PTM': 1.0,
        'FG%': 150,  # Very important (you're defending FG%)
        'FT%': 100,
        'TO': -1.0,  # Negative weight (less is better)
    }
    
    def calculate_player_value(player: Dict) -> float:
        """Calculate weighted value score for a player."""
        ros = player['ros_per_game']
        score = 0
        for cat, weight in weights.items():
            score += ros.get(cat, 0) * weight
        return score * player['trend_multiplier']
    
    give_value = sum(calculate_player_value(p) for p in give_players)
    get_value = sum(calculate_player_value(p) for p in get_players)
    
    return {
        'ros_value_give': give_value,
        'ros_value_get': get_value,
        'value_diff': get_value - give_value,
        'value_ratio': get_value / give_value if give_value > 0 else 0,
    }


# ============================================================================
# DISPLAY FUNCTIONS
# ============================================================================

def print_player_card(player: Dict, label: str = ""):
    """Print detailed player card."""
    print(f"\n{'━' * 90}")
    print(f"🏀 {player['name']} ({player['team']}) - {player['position']}")
    if label:
        print(f"   {label}")
    print(f"{'━' * 90}")
    
    print(f"   Status: {player['status'] if player['status'] else 'Healthy'}")
    print(f"   Injury Risk: {player['injury_risk']}")
    print(f"   Recent Trend: {player['trend']}")
    
    # Season stats
    print(f"\n   SEASON AVERAGES ({GAMES_PLAYED_SO_FAR} games):")
    s = player['season_per_game']
    print(f"      {s['PTS']:.1f} PTS | {s['REB']:.1f} REB | {s['AST']:.1f} AST | "
          f"{s['ST']:.1f} STL | {s['BLK']:.1f} BLK | {s['3PTM']:.1f} 3PM")
    print(f"      {s['FG%']:.3f} FG% | {s['FT%']:.3f} FT% | {s['TO']:.1f} TO")
    
    # Recent stats
    if player['recent_per_game']:
        r = player['recent_per_game']
        print(f"\n   LAST 7 DAYS (per game):")
        print(f"      {r['PTS']:.1f} PTS | {r['REB']:.1f} REB | {r['AST']:.1f} AST | "
              f"{r['ST']:.1f} STL | {r['BLK']:.1f} BLK | {r['3PTM']:.1f} 3PM")
        print(f"      {r['FG%']:.3f} FG% | {r['FT%']:.3f} FT% | {r['TO']:.1f} TO")
    
    # ROS projection
    ros = player['ros_per_game']
    print(f"\n   REST-OF-SEASON PROJECTION (weighted):")
    print(f"      {ros['PTS']:.1f} PTS | {ros['REB']:.1f} REB | {ros['AST']:.1f} AST | "
          f"{ros['ST']:.1f} STL | {ros['BLK']:.1f} BLK | {ros['3PTM']:.1f} 3PM")
    print(f"      {ros['FG%']:.3f} FG% | {ros['FT%']:.3f} FT% | {ros['TO']:.1f} TO")


def print_trade_summary(give_players: List[Dict], get_players: List[Dict], 
                       impact: Dict, value: Dict, other_team_name: str = "Other Team"):
    """Print complete trade summary."""
    print("\n" + "=" * 100)
    print("🔄" + " " * 40 + "TRADE SUMMARY" + " " * 40 + "🔄")
    print("=" * 100)
    
    print(f"\n📤 YOU GIVE:")
    for p in give_players:
        print(f"   • {p['name']} ({p['team']}) - {p['position']}")
    
    print(f"\n📥 YOU GET:")
    for p in get_players:
        print(f"   • {p['name']} ({p['team']}) - {p['position']}")
    
    print(f"\n🤝 TRADE PARTNER: {other_team_name}")
    
    # Category impact
    print("\n" + "=" * 100)
    print("📊 CATEGORY IMPACT (Rest of Season)")
    print("=" * 100)
    
    print(f"\n{'Category':<10} {'You Give':>12} {'You Get':>12} {'Net Change':>12} {'Result':>15}")
    print("-" * 65)
    
    for cat in CATEGORIES:
        if cat in PUNT_CATEGORIES:
            continue
        
        give = impact['give_totals'][cat]
        get = impact['get_totals'][cat]
        change = impact['net_change'][cat]
        
        if cat in impact['winners']:
            result = '✅ WIN'
        elif cat in impact['losers']:
            result = '❌ LOSE'
        else:
            result = '➖ NEUTRAL'
        
        if cat in ['FG%', 'FT%']:
            print(f"{cat:<10} {give:>12.3f} {get:>12.3f} {change:>+12.3f} {result:>15}")
        else:
            print(f"{cat:<10} {give:>12.1f} {get:>12.1f} {change:>+12.1f} {result:>15}")
    
    # Summary
    print("\n" + "=" * 100)
    print("🎯 VERDICT")
    print("=" * 100)
    
    print(f"\n   Categories You IMPROVE: {len(impact['winners'])} - {', '.join(impact['winners'])}")
    print(f"   Categories You HURT: {len(impact['losers'])} - {', '.join(impact['losers']) if impact['losers'] else 'None'}")
    print(f"   Neutral Categories: {len(impact['neutral'])} - {', '.join(impact['neutral']) if impact['neutral'] else 'None'}")
    
    print(f"\n   Overall Value Ratio: {value['value_ratio']:.2f}x")
    print(f"   Net Value: {value['value_diff']:+.1f}")
    
    if value['value_ratio'] >= 1.15:
        print(f"\n   ✅ STRONG ACCEPT - You're getting significantly more value")
    elif value['value_ratio'] >= 1.05:
        print(f"\n   ✅ ACCEPT - You're getting better value")
    elif value['value_ratio'] >= 0.95:
        print(f"\n   🤔 FAIR TRADE - Roughly even value")
    elif value['value_ratio'] >= 0.85:
        print(f"\n   ⚠️  DECLINE - You're giving up more value")
    else:
        print(f"\n   ❌ STRONG DECLINE - You're getting significantly worse value")
    
    # Injury considerations
    injured_give = [p for p in give_players if p['status'] in ['INJ', 'O', 'GTD', 'DTD']]
    injured_get = [p for p in get_players if p['status'] in ['INJ', 'O', 'GTD', 'DTD']]
    
    if injured_give or injured_get:
        print(f"\n   ⚕️  INJURY CONSIDERATIONS:")
        if injured_give:
            for p in injured_give:
                print(f"      • Giving away {p['name']}: {p['status']} - {p['injury_risk']}")
        if injured_get:
            for p in injured_get:
                print(f"      • Getting {p['name']}: {p['status']} - {p['injury_risk']}")


# ============================================================================
# MAIN ANALYSIS FUNCTION
# ============================================================================

def analyze_trade(give_player_names: List[str], get_player_names: List[str], 
                 other_team_key: str = None):
    """
    Analyze a trade proposal.
    
    Args:
        give_player_names: List of player names you're trading away
        get_player_names: List of player names you're receiving
        other_team_key: Team key of trade partner (optional, for display)
    """
    print("\n" + "=" * 100)
    print("🔄" + " " * 35 + "TRADE ANALYZER - LOADING" + " " * 35 + "🔄")
    print("=" * 100)
    
    oauth = ensure_oauth()
    
    # Get other team name
    other_team_name = "Other Team"
    if other_team_key:
        other_team_name = get_team_name(oauth, other_team_key)
    
    # Fetch player profiles
    print("\n📥 Fetching player data...")
    
    give_players = []
    for name in give_player_names:
        print(f"   Loading: {name}...")
        profile = get_full_player_profile(oauth, name)
        if profile:
            give_players.append(profile)
        else:
            print(f"   ⚠️  Could not find {name}")
    
    get_players = []
    for name in get_player_names:
        print(f"   Loading: {name}...")
        profile = get_full_player_profile(oauth, name)
        if profile:
            get_players.append(profile)
        else:
            print(f"   ⚠️  Could not find {name}")
    
    if not give_players or not get_players:
        print("\n❌ Error: Could not load all players")
        return
    
    # Show detailed player cards
    print("\n" + "=" * 100)
    print("📋 PLAYER DETAILS")
    print("=" * 100)
    
    print("\n📤 PLAYERS YOU'RE GIVING UP:")
    for p in give_players:
        print_player_card(p)
    
    print("\n\n📥 PLAYERS YOU'RE RECEIVING:")
    for p in get_players:
        print_player_card(p)
    
    # Analyze impact
    impact = calculate_category_impact(give_players, get_players, PUNT_CATEGORIES)
    value = evaluate_trade_value(give_players, get_players)
    
    # Print summary
    print_trade_summary(give_players, get_players, impact, value, other_team_name)
    
    print("\n" + "=" * 100)
    print("🔄" + " " * 35 + "END OF ANALYSIS" + " " * 35 + "🔄")
    print("=" * 100 + "\n")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    # OPTION 1: McBride + Clowney for OG Anunoby
    # OG = elite FG%, good defense, low TO
    
    print("\n🎯 TRADE OPTION 1: Dump dead weight for elite wing")
    print("   Target: OG Anunoby (owned by 'King Ant 👑🐜')")
    print("   Give: Miles McBride + Noah Clowney\n")
    
    analyze_trade(
        give_player_names=['Miles McBride', 'Noah Clowney'],
        get_player_names=['OG Anunoby'],
        other_team_key='466.l.10145.t.6'  # King Ant
    )
