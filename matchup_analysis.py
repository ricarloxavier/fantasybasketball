#!/usr/bin/env python3
"""
🏀 WEEKLY MATCHUP ANALYSIS 🏀
Analyzes strengths and weaknesses for your upcoming matchup.
"""

import os
import sys
from collections import defaultdict
from typing import Dict, List, Any

from yahoo_fantasy_api import game
from yahoo_oauth import OAuth2


# Stat ID mapping
STAT_MAP = {
    '5': 'FG%', '8': 'FT%', '10': '3PTM', '12': 'PTS', 
    '15': 'REB', '16': 'AST', '17': 'ST', '18': 'BLK', '19': 'TO'
}

# Categories where lower is better
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


def api_get(oauth: OAuth2, url: str) -> Dict[str, Any]:
    """Make API request."""
    resp = oauth.session.get(url)
    resp.raise_for_status()
    return resp.json()


def get_team_roster_keys(oauth: OAuth2, team_key: str) -> List[str]:
    """Get player keys for a team's roster."""
    url = f'https://fantasysports.yahooapis.com/fantasy/v2/team/{team_key}/roster/players?format=json'
    data = api_get(oauth, url)
    
    roster = data['fantasy_content']['team'][1]['roster']
    players_section = roster['0']['players']
    
    player_keys = []
    for key in players_section:
        if key == 'count':
            continue
        player = players_section[key]['player']
        for item in player[0]:
            if isinstance(item, dict) and 'player_key' in item:
                player_keys.append(item['player_key'])
                break
    
    return player_keys


def get_players_season_stats(oauth: OAuth2, league_key: str, player_keys: List[str]) -> Dict[str, Dict]:
    """Get season stats for multiple players."""
    keys_str = ','.join(player_keys)
    url = f'https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;player_keys={keys_str}/stats?format=json'
    data = api_get(oauth, url)
    
    players = data['fantasy_content']['league'][1]['players']
    results = {}
    
    for key in players:
        if key == 'count':
            continue
        player = players[key]['player']
        
        name = ''
        player_key = ''
        position = ''
        team_abbr = ''
        
        for item in player[0]:
            if isinstance(item, dict):
                if 'name' in item:
                    name = item['name']['full']
                if 'player_key' in item:
                    player_key = item['player_key']
                if 'display_position' in item:
                    position = item['display_position']
                if 'editorial_team_abbr' in item:
                    team_abbr = item['editorial_team_abbr']
        
        stats = {}
        player_stats = player[1].get('player_stats', {}).get('stats', [])
        for stat in player_stats:
            stat_data = stat.get('stat', {})
            stat_id = str(stat_data.get('stat_id', ''))
            value = stat_data.get('value', '')
            if stat_id in STAT_MAP and value not in ['-', '']:
                try:
                    stats[STAT_MAP[stat_id]] = float(value)
                except:
                    pass
        
        results[player_key] = {
            'name': name,
            'position': position,
            'nba_team': team_abbr,
            'stats': stats
        }
    
    return results


def get_player_weekly_games(oauth: OAuth2, league_key: str, player_key: str, week: int) -> int:
    """Get number of games a player has this week. This is an approximation."""
    # For now, return a default. In real implementation, you'd query the schedule.
    return 4  # Average games per week


def calculate_team_totals(players: Dict[str, Dict], player_keys: List[str]) -> Dict[str, float]:
    """Calculate projected totals for a team."""
    totals = defaultdict(float)
    fg_made = 0
    fg_att = 0
    ft_made = 0
    ft_att = 0
    
    for pk in player_keys:
        if pk not in players:
            continue
        stats = players[pk]['stats']
        
        for cat in ['3PTM', 'PTS', 'REB', 'AST', 'ST', 'BLK', 'TO']:
            if cat in stats:
                totals[cat] += stats[cat]
        
        # For percentages, we'll use weighted average approach
        if 'FG%' in stats and 'PTS' in stats:
            # Estimate attempts from points (rough approximation)
            pts = stats['PTS']
            fg_pct = stats['FG%']
            # This is very rough - just for comparison purposes
            fg_made += pts * 0.4  # rough estimate
            fg_att += (pts * 0.4) / fg_pct if fg_pct > 0 else 0
        
        if 'FT%' in stats and 'PTS' in stats:
            pts = stats['PTS']
            ft_pct = stats['FT%']
            ft_made += pts * 0.15  # rough estimate
            ft_att += (pts * 0.15) / ft_pct if ft_pct > 0 else 0
    
    totals['FG%'] = fg_made / fg_att if fg_att > 0 else 0
    totals['FT%'] = ft_made / ft_att if ft_att > 0 else 0
    
    return dict(totals)


def calculate_per_game_averages(players: Dict[str, Dict], player_keys: List[str]) -> Dict[str, float]:
    """Calculate per-game averages for a team."""
    totals = defaultdict(float)
    games = 0
    
    for pk in player_keys:
        if pk not in players:
            continue
        stats = players[pk]['stats']
        # Assume ~35 games played so far in season
        games += 35
        
        for cat in ['3PTM', 'PTS', 'REB', 'AST', 'ST', 'BLK', 'TO']:
            if cat in stats:
                totals[cat] += stats[cat]
    
    # Per game averages
    if games > 0:
        for cat in totals:
            totals[cat] = totals[cat] / (games / 15)  # Divide by number of players worth of games
    
    return dict(totals)


def analyze_matchup(my_stats: Dict, opp_stats: Dict) -> Dict[str, Dict]:
    """Analyze category-by-category matchup."""
    analysis = {}
    
    categories = ['FG%', 'FT%', '3PTM', 'PTS', 'REB', 'AST', 'ST', 'BLK', 'TO']
    
    for cat in categories:
        my_val = my_stats.get(cat, 0)
        opp_val = opp_stats.get(cat, 0)
        
        if cat == 'TO':
            # Lower is better for TO
            diff = opp_val - my_val
            advantage = 'YOU' if my_val < opp_val else 'OPP' if opp_val < my_val else 'TIE'
        else:
            diff = my_val - opp_val
            advantage = 'YOU' if my_val > opp_val else 'OPP' if opp_val > my_val else 'TIE'
        
        # Calculate percentage difference
        if cat in ['FG%', 'FT%']:
            pct_diff = (my_val - opp_val) * 100  # Already a percentage
        else:
            base = max(my_val, opp_val, 1)
            pct_diff = (diff / base) * 100
        
        analysis[cat] = {
            'my_value': my_val,
            'opp_value': opp_val,
            'difference': diff,
            'pct_difference': pct_diff,
            'advantage': advantage
        }
    
    return analysis


def analyze_player_value(player: Dict, team_needs: List[str]) -> Dict:
    """Analyze a player's value based on team needs."""
    stats = player['stats']
    
    value_scores = {}
    for cat in team_needs:
        if cat in stats:
            value_scores[cat] = stats.get(cat, 0)
    
    return {
        'name': player['name'],
        'position': player['position'],
        'nba_team': player['nba_team'],
        'stats': stats,
        'need_scores': value_scores
    }


def print_matchup_analysis(my_team_name: str, opp_team_name: str, 
                           my_players: Dict, opp_players: Dict,
                           my_keys: List[str], opp_keys: List[str],
                           analysis: Dict, week: int):
    """Print detailed matchup analysis."""
    
    print("\n" + "="*80)
    print("🏀" + " "*25 + "WEEKLY MATCHUP ANALYSIS" + " "*25 + "🏀")
    print("="*80)
    print(f"\n📅 Week {week}")
    print(f"\n🆚 {my_team_name} vs {opp_team_name}")
    print("="*80)
    
    # Category breakdown
    print("\n📊 CATEGORY-BY-CATEGORY ANALYSIS (Season Totals)")
    print("-"*80)
    print(f"{'Category':<10} {'You':>12} {'Opponent':>12} {'Diff':>10} {'Advantage':>12}")
    print("-"*80)
    
    my_wins = 0
    opp_wins = 0
    
    for cat in ['FG%', 'FT%', '3PTM', 'PTS', 'REB', 'AST', 'ST', 'BLK', 'TO']:
        data = analysis[cat]
        
        if cat in ['FG%', 'FT%']:
            my_str = f"{data['my_value']:.3f}"
            opp_str = f"{data['opp_value']:.3f}"
            diff_str = f"{data['difference']:+.3f}"
        else:
            my_str = f"{data['my_value']:.0f}"
            opp_str = f"{data['opp_value']:.0f}"
            diff_str = f"{data['difference']:+.0f}"
        
        adv = data['advantage']
        if adv == 'YOU':
            adv_str = "✅ YOU"
            my_wins += 1
        elif adv == 'OPP':
            adv_str = "❌ OPP"
            opp_wins += 1
        else:
            adv_str = "➖ TIE"
        
        print(f"{cat:<10} {my_str:>12} {opp_str:>12} {diff_str:>10} {adv_str:>12}")
    
    print("-"*80)
    print(f"\n🏆 PROJECTED SCORE: {my_wins}-{opp_wins}" + ("-0 (TIE categories)" if (my_wins + opp_wins) < 9 else ""))
    
    # Identify strengths and weaknesses
    print("\n" + "="*80)
    print("💪 YOUR STRENGTHS (Categories to protect)")
    print("-"*80)
    
    strengths = [(cat, data) for cat, data in analysis.items() 
                 if data['advantage'] == 'YOU']
    strengths.sort(key=lambda x: abs(x[1]['pct_difference']), reverse=True)
    
    for cat, data in strengths:
        print(f"  ✅ {cat}: You lead by {abs(data['difference']):.1f}" + 
              (f" ({abs(data['pct_difference']):.1f}%)" if cat not in ['FG%', 'FT%'] else ""))
    
    print("\n" + "="*80)
    print("⚠️  YOUR WEAKNESSES (Categories to target/stream for)")
    print("-"*80)
    
    weaknesses = [(cat, data) for cat, data in analysis.items() 
                  if data['advantage'] == 'OPP']
    weaknesses.sort(key=lambda x: abs(x[1]['pct_difference']), reverse=True)
    
    for cat, data in weaknesses:
        print(f"  ❌ {cat}: You trail by {abs(data['difference']):.1f}" +
              (f" ({abs(data['pct_difference']):.1f}%)" if cat not in ['FG%', 'FT%'] else ""))
    
    close_cats = [(cat, data) for cat, data in analysis.items() 
                  if abs(data['pct_difference']) < 10 and cat not in ['FG%', 'FT%']]
    
    if close_cats:
        print("\n" + "="*80)
        print("🎯 CLOSE CATEGORIES (Swing categories - target these!)")
        print("-"*80)
        for cat, data in close_cats:
            status = "✅" if data['advantage'] == 'YOU' else "❌" if data['advantage'] == 'OPP' else "➖"
            print(f"  {status} {cat}: Difference of only {abs(data['difference']):.1f}")
    
    # Roster breakdown
    print("\n" + "="*80)
    print("📋 YOUR ROSTER")
    print("-"*80)
    print(f"{'Player':<22} {'Pos':<8} {'PTS':>6} {'REB':>5} {'AST':>5} {'STL':>4} {'BLK':>4} {'3PM':>4} {'TO':>4}")
    print("-"*80)
    
    for pk in my_keys:
        if pk not in my_players:
            continue
        p = my_players[pk]
        s = p['stats']
        print(f"{p['name'][:21]:<22} {p['position'][:7]:<8} "
              f"{s.get('PTS', 0):>6.0f} {s.get('REB', 0):>5.0f} {s.get('AST', 0):>5.0f} "
              f"{s.get('ST', 0):>4.0f} {s.get('BLK', 0):>4.0f} {s.get('3PTM', 0):>4.0f} {s.get('TO', 0):>4.0f}")
    
    print("\n" + "="*80)
    print("📋 OPPONENT'S ROSTER")
    print("-"*80)
    print(f"{'Player':<22} {'Pos':<8} {'PTS':>6} {'REB':>5} {'AST':>5} {'STL':>4} {'BLK':>4} {'3PM':>4} {'TO':>4}")
    print("-"*80)
    
    for pk in opp_keys:
        if pk not in opp_players:
            continue
        p = opp_players[pk]
        s = p['stats']
        print(f"{p['name'][:21]:<22} {p['position'][:7]:<8} "
              f"{s.get('PTS', 0):>6.0f} {s.get('REB', 0):>5.0f} {s.get('AST', 0):>5.0f} "
              f"{s.get('ST', 0):>4.0f} {s.get('BLK', 0):>4.0f} {s.get('3PTM', 0):>4.0f} {s.get('TO', 0):>4.0f}")
    
    return weaknesses


def analyze_drop_candidate(player: Dict, weaknesses: List, team_players: Dict, team_keys: List[str]):
    """Analyze whether to drop a specific player for streaming."""
    
    print("\n" + "="*80)
    print(f"🔍 DROP ANALYSIS: {player['name']}")
    print("="*80)
    
    stats = player['stats']
    
    print(f"\n📊 {player['name']}'s Season Stats:")
    print(f"   Position: {player['position']}")
    print(f"   NBA Team: {player['nba_team']}")
    print("-"*40)
    
    for cat in ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO', 'FG%', 'FT%']:
        if cat in stats:
            val = stats[cat]
            if cat in ['FG%', 'FT%']:
                print(f"   {cat}: {val:.3f}")
            else:
                print(f"   {cat}: {val:.0f}")
    
    # Calculate player's contribution to weak categories
    weak_cats = [w[0] for w in weaknesses]
    
    print(f"\n🎯 Contribution to Your WEAK Categories:")
    weak_contribution = 0
    for cat in weak_cats:
        if cat in stats and cat not in ['FG%', 'FT%']:
            print(f"   {cat}: {stats.get(cat, 0):.0f}")
            weak_contribution += stats.get(cat, 0)
    
    # Compare to team average
    print(f"\n📈 Comparison to Team Average:")
    
    team_totals = defaultdict(float)
    for pk in team_keys:
        if pk in team_players:
            for cat, val in team_players[pk]['stats'].items():
                team_totals[cat] += val
    
    num_players = len(team_keys)
    
    below_avg_cats = []
    above_avg_cats = []
    
    for cat in ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM']:
        if cat in stats:
            team_avg = team_totals[cat] / num_players
            player_val = stats[cat]
            diff = player_val - team_avg
            pct = (diff / team_avg * 100) if team_avg > 0 else 0
            
            status = "✅" if diff > 0 else "❌"
            print(f"   {cat}: {player_val:.0f} vs team avg {team_avg:.0f} ({diff:+.0f}, {pct:+.1f}%) {status}")
            
            if diff < 0:
                below_avg_cats.append(cat)
            else:
                above_avg_cats.append(cat)
    
    # Streaming value analysis
    print(f"\n💡 STREAMING SPOT ANALYSIS:")
    print("-"*40)
    
    # Check if player helps weak categories
    helps_weak = [cat for cat in weak_cats if cat in above_avg_cats]
    hurts_weak = [cat for cat in weak_cats if cat in below_avg_cats]
    
    if hurts_weak:
        print(f"   ⚠️  BELOW AVERAGE in weak categories: {', '.join(hurts_weak)}")
        print(f"      → Dropping could help if you stream for these cats")
    
    if helps_weak:
        print(f"   ✅ ABOVE AVERAGE in weak categories: {', '.join(helps_weak)}")
        print(f"      → Be careful - dropping hurts these categories")
    
    # Final recommendation
    print(f"\n🎲 RECOMMENDATION:")
    print("-"*40)
    
    # Simple scoring: +1 for each weak cat below avg, -1 for each weak cat above avg
    drop_score = len(hurts_weak) - len(helps_weak)
    
    # Also consider if player is generally below average
    general_below = len(below_avg_cats)
    general_above = len(above_avg_cats)
    
    if drop_score > 0 and general_below >= 3:
        print(f"   ✅ CONSIDER DROPPING for streaming")
        print(f"      - Below team average in {general_below} categories")
        print(f"      - Doesn't help your weak categories much")
        print(f"      - Streaming could target: {', '.join(weak_cats)}")
    elif drop_score < 0:
        print(f"   ❌ KEEP - Helps your weak categories")
        print(f"      - Above average in weak cats: {', '.join(helps_weak)}")
    else:
        print(f"   ➖ BORDERLINE - Consider matchup and schedule")
        print(f"      - Above average in: {', '.join(above_avg_cats)}")
        print(f"      - Below average in: {', '.join(below_avg_cats)}")
    
    return drop_score


def main():
    oauth = ensure_oauth("oauth2.json")
    gm = game.Game(oauth, "nba")
    lg = gm.to_league("466.l.10145")

    league_key = "466.l.10145"
    current_week = lg.current_week()

    # Your team
    my_team_key = "466.l.10145.t.12"

    # Auto-detect opponent for current week
    my_team = lg.to_team(my_team_key)
    opp_team_key = my_team.matchup(current_week)

    # Resolve team names from league
    teams = lg.teams()
    my_team_name = teams.get(my_team_key, {}).get("name", "Your Team")
    opp_team_name = teams.get(opp_team_key, {}).get("name", "Opponent")

    print("\n" + "="*80)
    print("🏀" + " "*20 + "MATCHUP ANALYSIS TOOL" + " "*20 + "🏀")
    print("="*80)
    print(f"\n📅 Week {current_week}: {my_team_name} vs {opp_team_name}")
    
    # Get rosters
    print("\n📥 Fetching rosters...")
    my_keys = get_team_roster_keys(oauth, my_team_key)
    opp_keys = get_team_roster_keys(oauth, opp_team_key)
    
    print(f"   Your roster: {len(my_keys)} players")
    print(f"   Opponent roster: {len(opp_keys)} players")
    
    # Get player stats
    print("\n📊 Fetching player stats...")
    all_keys = my_keys + opp_keys
    all_players = get_players_season_stats(oauth, league_key, all_keys)
    
    my_players = {k: v for k, v in all_players.items() if k in my_keys}
    opp_players = {k: v for k, v in all_players.items() if k in opp_keys}
    
    # Calculate totals
    my_totals = calculate_team_totals(my_players, my_keys)
    opp_totals = calculate_team_totals(opp_players, opp_keys)
    
    # Analyze matchup
    analysis = analyze_matchup(my_totals, opp_totals)
    
    # Print analysis
    weaknesses = print_matchup_analysis(
        my_team_name, opp_team_name,
        my_players, opp_players,
        my_keys, opp_keys,
        analysis, current_week
    )
    
    # Analyze Maxime Raynaud as drop candidate
    raynaud_key = None
    for pk, player in my_players.items():
        if 'Raynaud' in player['name']:
            raynaud_key = pk
            break
    
    if raynaud_key:
        analyze_drop_candidate(my_players[raynaud_key], weaknesses, my_players, my_keys)
    
    print("\n" + "="*80)
    print("🏀" + " "*20 + "END OF ANALYSIS" + " "*20 + "🏀")
    print("="*80)


if __name__ == "__main__":
    main()
