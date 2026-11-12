#!/usr/bin/env python3
"""
🏀 STREAMING ANALYSIS FOR WEEK 12 🏀
Analyzes free agents and identifies best streaming options.
"""

import os
import sys
from collections import defaultdict
from typing import Dict, List, Any

from yahoo_fantasy_api import game
from yahoo_oauth import OAuth2


# Games per team for Week 12 (Jan 5-11, 2026)
# Based on NBA schedule
GAMES_THIS_WEEK = {
    'HOU': 4, 'POR': 4, 'MEM': 4, 'PHX': 4, 'OKC': 4,
    'UTA': 4, 'BOS': 4, 'DEN': 4, 'CHA': 4, 'ATL': 4,
    'TOR': 4, 'WAS': 4, 'MIN': 4,
    'NYK': 3, 'DET': 3, 'CHI': 3, 'PHI': 3, 'ORL': 3,
    'NOP': 3, 'IND': 3, 'CLE': 3, 'MIA': 3, 'SAS': 3,
    'DAL': 3, 'SAC': 3, 'GSW': 3, 'LAC': 3, 'LAL': 3,
    'MIL': 3, 'BKN': 3,
}

STAT_MAP = {
    '5': 'FG%', '8': 'FT%', '10': '3PTM', '12': 'PTS', 
    '15': 'REB', '16': 'AST', '17': 'ST', '18': 'BLK', '19': 'TO'
}


def ensure_oauth(oauth_path: str) -> OAuth2:
    if not os.path.exists(oauth_path):
        print(f"oauth2 file not found at {oauth_path}", file=sys.stderr)
        sys.exit(1)
    oauth = OAuth2(None, None, from_file=oauth_path)
    if not oauth.token_is_valid():
        oauth.refresh_access_token()
    return oauth


def api_get(oauth: OAuth2, url: str) -> Dict[str, Any]:
    resp = oauth.session.get(url)
    resp.raise_for_status()
    return resp.json()


def get_free_agents(oauth: OAuth2, league_key: str, count: int = 100) -> List[Dict]:
    """Get free agents with their stats."""
    players_list = []
    
    for start in [0, 50]:
        url = f'https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;status=FA;sort=OR;start={start};count=50/stats?format=json'
        data = api_get(oauth, url)
        
        players = data['fantasy_content']['league'][1]['players']
        
        for key in players:
            if key == 'count':
                continue
            player = players[key]['player']
            
            name = ''
            position = ''
            team = ''
            player_key = ''
            
            for item in player[0]:
                if isinstance(item, dict):
                    if 'name' in item:
                        name = item['name']['full']
                    if 'display_position' in item:
                        position = item['display_position']
                    if 'editorial_team_abbr' in item:
                        team = item['editorial_team_abbr']
                    if 'player_key' in item:
                        player_key = item['player_key']
            
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
            
            # Skip players with no stats (injured/out)
            if stats.get('PTS', 0) > 0:
                players_list.append({
                    'name': name,
                    'position': position,
                    'team': team,
                    'player_key': player_key,
                    'stats': stats,
                    'games_this_week': GAMES_THIS_WEEK.get(team, 3)
                })
    
    return players_list


def get_my_roster(oauth: OAuth2, team_key: str, league_key: str) -> List[Dict]:
    """Get my team's roster with stats."""
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
    
    # Get stats for these players
    keys_str = ','.join(player_keys)
    url = f'https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;player_keys={keys_str}/stats?format=json'
    data = api_get(oauth, url)
    
    players = data['fantasy_content']['league'][1]['players']
    roster_list = []
    
    for key in players:
        if key == 'count':
            continue
        player = players[key]['player']
        
        name = ''
        position = ''
        team = ''
        player_key = ''
        
        for item in player[0]:
            if isinstance(item, dict):
                if 'name' in item:
                    name = item['name']['full']
                if 'display_position' in item:
                    position = item['display_position']
                if 'editorial_team_abbr' in item:
                    team = item['editorial_team_abbr']
                if 'player_key' in item:
                    player_key = item['player_key']
        
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
        
        roster_list.append({
            'name': name,
            'position': position,
            'team': team,
            'player_key': player_key,
            'stats': stats,
            'games_this_week': GAMES_THIS_WEEK.get(team, 3)
        })
    
    return roster_list


def calculate_weekly_projection(player: Dict, games: int = None) -> Dict[str, float]:
    """Project stats for the week based on per-game averages."""
    stats = player['stats']
    games_played = 35  # Approximate games played so far
    
    if games is None:
        games = player.get('games_this_week', 3)
    
    weekly = {}
    for cat in ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO']:
        if cat in stats:
            per_game = stats[cat] / games_played
            weekly[cat] = per_game * games
    
    weekly['FG%'] = stats.get('FG%', 0)
    weekly['FT%'] = stats.get('FT%', 0)
    
    return weekly


def score_for_needs(weekly_stats: Dict[str, float], weak_cats: List[str]) -> float:
    """Score a player based on how much they help weak categories."""
    score = 0
    
    # Weight categories by importance
    weights = {
        'TO': 3.0,   # Most important - you're losing badly
        'FT%': 2.0,  # Second most important
        'FG%': 1.5,  # Third
        'PTS': 0.5,
        'REB': 0.3,
        'AST': 0.3,
        'ST': 0.5,
        'BLK': 0.5,
        '3PTM': 0.5
    }
    
    for cat in weak_cats:
        if cat in weekly_stats:
            if cat == 'TO':
                # Lower TO is better - penalize high TO
                score -= weekly_stats[cat] * weights[cat]
            elif cat in ['FG%', 'FT%']:
                # Higher percentage is better
                score += weekly_stats[cat] * 100 * weights[cat]
            else:
                score += weekly_stats[cat] * weights[cat]
    
    return score


def main():
    oauth = ensure_oauth("oauth2.json")
    gm = game.Game(oauth, "nba")
    lg = gm.to_league("466.l.10145")
    
    league_key = "466.l.10145"
    my_team_key = "466.l.10145.t.12"
    
    print("\n" + "="*80)
    print("🏀" + " "*20 + "STREAMING ANALYSIS - WEEK 12" + " "*20 + "🏀")
    print("="*80)
    
    # Your weak categories from matchup analysis
    weak_cats = ['TO', 'FT%', 'FG%']
    print(f"\n🎯 Targeting categories: {', '.join(weak_cats)}")
    
    # Teams with 4 games
    print("\n📅 TEAMS WITH 4 GAMES THIS WEEK:")
    print("-"*50)
    four_game_teams = [t for t, g in GAMES_THIS_WEEK.items() if g >= 4]
    print(f"   {', '.join(sorted(four_game_teams))}")
    
    # Get free agents
    print("\n📥 Fetching free agents...")
    free_agents = get_free_agents(oauth, league_key)
    print(f"   Found {len(free_agents)} available players with stats")
    
    # Get my roster
    print("\n📋 Fetching your roster...")
    my_roster = get_my_roster(oauth, my_team_key, league_key)
    print(f"   Your roster: {len(my_roster)} players")
    
    # Calculate weekly projections for free agents
    for fa in free_agents:
        fa['weekly'] = calculate_weekly_projection(fa)
        fa['need_score'] = score_for_needs(fa['weekly'], weak_cats)
    
    # Sort by need score (higher is better)
    free_agents.sort(key=lambda x: x['need_score'], reverse=True)
    
    # Filter to 4-game teams for streaming
    four_game_fas = [fa for fa in free_agents if fa['games_this_week'] >= 4]
    
    print("\n" + "="*80)
    print("🌟 TOP STREAMING OPTIONS (4-game teams, sorted by your needs)")
    print("="*80)
    print(f"\n{'Rank':<4} {'Player':<22} {'Team':<5} {'Games':<5} {'TO':>5} {'FT%':>6} {'FG%':>6} {'Score':>8}")
    print("-"*80)
    
    for i, fa in enumerate(four_game_fas[:15], 1):
        w = fa['weekly']
        print(f"{i:<4} {fa['name'][:21]:<22} {fa['team']:<5} {fa['games_this_week']:<5} "
              f"{w.get('TO', 0):>5.1f} {w.get('FT%', 0):>6.3f} {w.get('FG%', 0):>6.3f} {fa['need_score']:>8.1f}")
    
    # Best overall streamers (all teams)
    print("\n" + "="*80)
    print("📊 BEST OVERALL STREAMERS (all teams)")
    print("="*80)
    print(f"\n{'Rank':<4} {'Player':<22} {'Team':<5} {'Games':<5} {'PTS':>5} {'REB':>4} {'AST':>4} {'STL':>4} {'BLK':>4} {'TO':>4}")
    print("-"*80)
    
    for i, fa in enumerate(free_agents[:20], 1):
        w = fa['weekly']
        print(f"{i:<4} {fa['name'][:21]:<22} {fa['team']:<5} {fa['games_this_week']:<5} "
              f"{w.get('PTS', 0):>5.1f} {w.get('REB', 0):>4.1f} {w.get('AST', 0):>4.1f} "
              f"{w.get('ST', 0):>4.1f} {w.get('BLK', 0):>4.1f} {w.get('TO', 0):>4.1f}")
    
    # Analyze drop candidates from roster
    print("\n" + "="*80)
    print("🔍 DROP CANDIDATE ANALYSIS")
    print("="*80)
    
    # Calculate weekly projections for roster
    for p in my_roster:
        p['weekly'] = calculate_weekly_projection(p)
        p['need_score'] = score_for_needs(p['weekly'], weak_cats)
    
    # Sort roster by need score (lowest = worst fit for needs)
    my_roster.sort(key=lambda x: x['need_score'])
    
    print(f"\nYour players ranked by fit for weak categories (lowest = best drop candidate):")
    print(f"\n{'Rank':<4} {'Player':<22} {'Team':<5} {'Games':<5} {'TO':>5} {'FT%':>6} {'FG%':>6} {'Score':>8}")
    print("-"*80)
    
    for i, p in enumerate(my_roster, 1):
        w = p['weekly']
        indicator = "❌ DROP?" if i <= 3 else ""
        print(f"{i:<4} {p['name'][:21]:<22} {p['team']:<5} {p['games_this_week']:<5} "
              f"{w.get('TO', 0):>5.1f} {w.get('FT%', 0):>6.3f} {w.get('FG%', 0):>6.3f} {p['need_score']:>8.1f} {indicator}")
    
    # Specific recommendations
    print("\n" + "="*80)
    print("💡 STREAMING RECOMMENDATIONS")
    print("="*80)
    
    print("\n🎯 BEST PICKUPS FOR YOUR WEAK CATEGORIES (TO, FT%, FG%):")
    print("-"*60)
    
    # Find best pickups that help TO (low TO), FT% (high), FG% (high)
    best_for_to = sorted([fa for fa in free_agents if fa['weekly'].get('TO', 99) < 2], 
                         key=lambda x: x['weekly'].get('TO', 99))[:5]
    
    best_for_ft = sorted([fa for fa in free_agents if fa['stats'].get('FT%', 0) > 0.8], 
                         key=lambda x: x['stats'].get('FT%', 0), reverse=True)[:5]
    
    best_for_fg = sorted([fa for fa in free_agents if fa['stats'].get('FG%', 0) > 0.5], 
                         key=lambda x: x['stats'].get('FG%', 0), reverse=True)[:5]
    
    print("\n📉 LOW TURNOVER SPECIALISTS (helps your TO deficit):")
    for fa in best_for_to[:5]:
        w = fa['weekly']
        print(f"   {fa['name']:<22} ({fa['team']}, {fa['games_this_week']}G) - TO: {w.get('TO', 0):.1f}/wk, FT%: {fa['stats'].get('FT%', 0):.3f}")
    
    print("\n🎯 HIGH FT% PLAYERS (helps your FT% deficit):")
    for fa in best_for_ft[:5]:
        w = fa['weekly']
        print(f"   {fa['name']:<22} ({fa['team']}, {fa['games_this_week']}G) - FT%: {fa['stats'].get('FT%', 0):.3f}, PTS: {w.get('PTS', 0):.1f}/wk")
    
    print("\n🎯 HIGH FG% PLAYERS (helps your FG% deficit):")
    for fa in best_for_fg[:5]:
        w = fa['weekly']
        print(f"   {fa['name']:<22} ({fa['team']}, {fa['games_this_week']}G) - FG%: {fa['stats'].get('FG%', 0):.3f}, PTS: {w.get('PTS', 0):.1f}/wk")
    
    print("\n" + "="*80)
    print("🏀" + " "*20 + "END OF STREAMING ANALYSIS" + " "*20 + "🏀")
    print("="*80)


if __name__ == "__main__":
    main()
