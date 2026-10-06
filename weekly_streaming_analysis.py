#!/usr/bin/env python3
"""
🏀 WEEKLY STREAMING ANALYSIS 🏀
Analyzes your matchup and recommends streaming moves.
Includes bench players, excludes injured (IL/IL+) players.

KEY FEATURES:
- Uses RECENT stats (last 7-14 days) weighted with season stats
- Considers injury opportunity (e.g., backup gets more mins when starter is out)
- Shows trending direction (hot/cold streaks)
- Identifies 4-game week teams for streaming
"""

import os
import sys
import requests
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Tuple, Optional

from yahoo_fantasy_api import game, League
from yahoo_oauth import OAuth2


# ============================================================================
# CONFIGURATION
# ============================================================================

LEAGUE_KEY = '466.l.10145'
MY_TEAM_KEY = '466.l.10145.t.12'

# Stat ID mapping (string keys to match API response)
STAT_MAP = {
    '5': 'FG%', '8': 'FT%', '10': '3PTM', '12': 'PTS',
    '15': 'REB', '16': 'AST', '17': 'ST', '18': 'BLK', '19': 'TO'
}

# Categories (excluding TO if punting)
CATEGORIES = ['FG%', 'FT%', '3PTM', 'PTS', 'REB', 'AST', 'ST', 'BLK']
PUNT_CATEGORIES = ['TO']  # Categories you're punting

# Week 12 schedule: Mon Jan 5 - Sun Jan 11, 2026
# Format: team_abbr -> number of games this week
# Using uppercase to match Yahoo API team abbreviations
WEEK_SCHEDULE = {
    # 4-game teams
    'ATL': 4, 'BOS': 4, 'CHA': 4, 'DEN': 4, 'HOU': 4, 'MEM': 4,
    'MIN': 4, 'OKC': 4, 'PHO': 4, 'POR': 4, 'TOR': 4, 'UTA': 4,
    # 3-game teams
    'WAS': 3, 'BKN': 3, 'CHI': 3, 'CLE': 3, 'DAL': 3, 'DET': 3,
    'GS': 3, 'GSW': 3, 'IND': 3, 'LAC': 3, 'LAL': 3, 'MIA': 3, 'MIL': 3,
    'NO': 3, 'NOP': 3, 'NY': 3, 'NYK': 3, 'ORL': 3, 'PHI': 3, 'SAC': 3, 'SA': 3, 'SAS': 3,
}

# Assumed games played in season so far (for per-game calculations).
# NBA season ~82 games over ~26 weeks; March is ~week 20 → ~60 games played.
GAMES_PLAYED = 60

# Recent stats weight (0.0 = all season, 1.0 = all recent)
# 0.6 means 60% recent, 40% season - favors recent performance
RECENT_WEIGHT = 0.6

# XML namespace for Yahoo API
YAHOO_NS = {'yh': 'http://fantasysports.yahooapis.com/fantasy/v2/base.rng'}

NBA_TEAM_CODES = {
    'ATL', 'BKN', 'BOS', 'CHA', 'CHI', 'CLE', 'DAL', 'DEN', 'DET', 'GSW',
    'HOU', 'IND', 'LAC', 'LAL', 'MEM', 'MIA', 'MIL', 'MIN', 'NOP', 'NYK',
    'OKC', 'ORL', 'PHI', 'PHX', 'POR', 'SAC', 'SAS', 'TOR', 'UTA', 'WSH',
}

TEAM_ABBR_ALIASES = {
    'GS': 'GSW', 'GSW': 'GSW', 'PHO': 'PHX', 'PHX': 'PHX',
    'NO': 'NOP', 'NOP': 'NOP', 'NY': 'NYK', 'NYK': 'NYK',
    'SA': 'SAS', 'SAS': 'SAS', 'BRK': 'BKN', 'BKN': 'BKN',
    'CHO': 'CHA', 'CHA': 'CHA', 'WAS': 'WSH', 'WSH': 'WSH',
    'UTA': 'UTA', 'UTAH': 'UTA',
}


def normalize_team(abbr: str) -> str:
    t = (abbr or '').strip().upper()
    return TEAM_ABBR_ALIASES.get(t, t)


def fetch_week_schedule(week_start: date, week_end: date) -> Dict[str, int]:
    """
    Fetch number of games per NBA team in the given date range using ESPN API.
    Falls back to a default of 3 games per team if ESPN is unavailable.
    """
    counts: Dict[str, int] = defaultdict(int)
    successful = 0
    for delta in range((week_end - week_start).days + 1):
        day = week_start + timedelta(days=delta)
        ds = day.strftime('%Y%m%d')
        url = f'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={ds}'
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            continue
        successful += 1
        for event in payload.get('events', []):
            competitions = event.get('competitions', [])
            if not competitions:
                continue
            for competitor in competitions[0].get('competitors', []):
                abbr = competitor.get('team', {}).get('abbreviation', '')
                team = normalize_team(abbr)
                if team in NBA_TEAM_CODES:
                    counts[team] += 1
    return dict(counts)


# ============================================================================
# OAUTH & API
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


def api_get(oauth: OAuth2, url: str) -> Dict[str, Any]:
    """Make API request."""
    resp = oauth.session.get(url)
    resp.raise_for_status()
    return resp.json()


def safe_float(val: Any) -> float:
    """Convert value to float, handling '-', empty, and None."""
    if val is None or val == '-' or val == '':
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ============================================================================
# RECENT STATS & CONTEXT ANALYSIS
# ============================================================================

def get_player_recent_stats(oauth: OAuth2, player_name: str, stat_type: str = 'lastweek') -> Optional[Dict]:
    """
    Get player stats for a specific time period.
    
    Args:
        oauth: OAuth2 credentials
        player_name: Player name to search for
        stat_type: 'lastweek', 'lastmonth', or 'season'
    
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
        owner_el = player.find('.//yh:ownership/yh:ownership_type', YAHOO_NS)
        
        # Extract stats
        stats = {}
        for stat in player.findall('.//yh:stat', YAHOO_NS):
            stat_id = stat.find('yh:stat_id', YAHOO_NS)
            value = stat.find('yh:value', YAHOO_NS)
            if stat_id is not None and value is not None:
                stats[stat_id.text] = value.text
        
        return {
            'name': name_el.text if name_el is not None else player_name,
            'team': team_el.text if team_el is not None else '',
            'position': pos_el.text if pos_el is not None else '',
            'status': status_el.text if status_el is not None else '',
            'is_owned': owner_el.text == 'team' if owner_el is not None else False,
            'stats': stats
        }
    except Exception as e:
        print(f"Error fetching stats for {player_name}: {e}")
        return None


def get_player_context(oauth: OAuth2, player_name: str) -> Dict:
    """
    Get full context for a player: season stats, recent stats, and trending.
    
    Returns dict with:
    - season_stats: Full season totals
    - recent_stats: Last 7 days totals
    - trend: 'HOT', 'COLD', or 'STEADY' based on recent vs season
    - opportunity_score: Higher if player has increased opportunity
    """
    season = get_player_recent_stats(oauth, player_name, 'season')
    recent = get_player_recent_stats(oauth, player_name, 'lastweek')
    
    if not season or not recent:
        return None
    
    # Parse stats
    s = season['stats']
    r = recent['stats']
    
    # Season per-game averages (assume ~35 games played)
    s_pts_pg = safe_float(s.get('12', 0)) / GAMES_PLAYED
    s_reb_pg = safe_float(s.get('15', 0)) / GAMES_PLAYED
    s_ast_pg = safe_float(s.get('16', 0)) / GAMES_PLAYED
    s_stl_pg = safe_float(s.get('17', 0)) / GAMES_PLAYED
    s_blk_pg = safe_float(s.get('18', 0)) / GAMES_PLAYED
    s_fg = safe_float(s.get('5', 0))
    
    # Recent per-game averages (last week = ~3 games typically)
    recent_games = 3  # Approximate games in last 7 days
    r_pts_pg = safe_float(r.get('12', 0)) / recent_games
    r_reb_pg = safe_float(r.get('15', 0)) / recent_games
    r_ast_pg = safe_float(r.get('16', 0)) / recent_games
    r_stl_pg = safe_float(r.get('17', 0)) / recent_games
    r_blk_pg = safe_float(r.get('18', 0)) / recent_games
    r_fg = safe_float(r.get('5', 0))
    
    # Calculate trend (compare recent per-game to season per-game)
    if s_pts_pg > 0:
        pts_ratio = r_pts_pg / s_pts_pg
    else:
        pts_ratio = 1.0
    
    if s_reb_pg > 0:
        reb_ratio = r_reb_pg / s_reb_pg
    else:
        reb_ratio = 1.0
    
    if s_ast_pg > 0:
        ast_ratio = r_ast_pg / s_ast_pg
    else:
        ast_ratio = 1.0
    
    # Average production ratio
    avg_ratio = (pts_ratio + reb_ratio + ast_ratio) / 3
    
    if avg_ratio >= 1.3:
        trend = 'HOT 🔥'
        trend_multiplier = 1.2
    elif avg_ratio >= 1.1:
        trend = 'WARM ↗️'
        trend_multiplier = 1.1
    elif avg_ratio <= 0.7:
        trend = 'COLD ❄️'
        trend_multiplier = 0.85
    elif avg_ratio <= 0.9:
        trend = 'COOL ↘️'
        trend_multiplier = 0.95
    else:
        trend = 'STEADY ➡️'
        trend_multiplier = 1.0
    
    # Weighted stats (blend recent with season)
    # Higher weight to recent for streaming decisions
    weighted_pts_pg = (r_pts_pg * RECENT_WEIGHT) + (s_pts_pg * (1 - RECENT_WEIGHT))
    weighted_reb_pg = (r_reb_pg * RECENT_WEIGHT) + (s_reb_pg * (1 - RECENT_WEIGHT))
    weighted_ast_pg = (r_ast_pg * RECENT_WEIGHT) + (s_ast_pg * (1 - RECENT_WEIGHT))
    weighted_stl_pg = (r_stl_pg * RECENT_WEIGHT) + (s_stl_pg * (1 - RECENT_WEIGHT))
    weighted_blk_pg = (r_blk_pg * RECENT_WEIGHT) + (s_blk_pg * (1 - RECENT_WEIGHT))
    weighted_fg = (r_fg * RECENT_WEIGHT) + (s_fg * (1 - RECENT_WEIGHT)) if r_fg > 0 else s_fg
    
    return {
        'name': season['name'],
        'team': season['team'],
        'position': season['position'],
        'status': season['status'],
        'is_owned': season.get('is_owned', False),
        'season_stats': {
            'PTS': safe_float(s.get('12', 0)),
            'REB': safe_float(s.get('15', 0)),
            'AST': safe_float(s.get('16', 0)),
            'ST': safe_float(s.get('17', 0)),
            'BLK': safe_float(s.get('18', 0)),
            '3PTM': safe_float(s.get('10', 0)),
            'FG%': s_fg,
            'FT%': safe_float(s.get('8', 0)),
        },
        'recent_stats': {
            'PTS': safe_float(r.get('12', 0)),
            'REB': safe_float(r.get('15', 0)),
            'AST': safe_float(r.get('16', 0)),
            'ST': safe_float(r.get('17', 0)),
            'BLK': safe_float(r.get('18', 0)),
            '3PTM': safe_float(r.get('10', 0)),
            'FG%': r_fg,
            'FT%': safe_float(r.get('8', 0)),
        },
        'weighted_per_game': {
            'PTS': weighted_pts_pg,
            'REB': weighted_reb_pg,
            'AST': weighted_ast_pg,
            'ST': weighted_stl_pg,
            'BLK': weighted_blk_pg,
            'FG%': weighted_fg,
        },
        'trend': trend,
        'trend_multiplier': trend_multiplier,
        'recent_vs_season_ratio': avg_ratio,
    }


def get_team_injuries(oauth: OAuth2, team_abbr: str) -> List[Dict]:
    """
    Get injured players for a team.
    Helps identify opportunity plays.
    """
    # Search for players on team with injury status
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}/players;status=A;sort=AR;count=50"
    headers = {'Authorization': f'Bearer {oauth.access_token}'}
    
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            return []
        
        root = ET.fromstring(resp.content)
        players = root.findall('.//yh:player', YAHOO_NS)
        
        injuries = []
        for p in players:
            team_el = p.find('.//yh:editorial_team_abbr', YAHOO_NS)
            status_el = p.find('.//yh:status', YAHOO_NS)
            name_el = p.find('.//yh:name/yh:full', YAHOO_NS)
            
            team = team_el.text if team_el is not None else ''
            status = status_el.text if status_el is not None else ''
            name = name_el.text if name_el is not None else ''
            
            if team.upper() == team_abbr.upper() and status in ['INJ', 'O', 'GTD']:
                injuries.append({
                    'name': name,
                    'team': team,
                    'status': status
                })
        
        return injuries
    except:
        return []


def find_streaming_candidates(oauth: OAuth2, target_categories: List[str] = None, 
                               plays_today: bool = False) -> List[Dict]:
    """
    Find the best streaming candidates based on current context.
    
    Args:
        oauth: OAuth2 credentials
        target_categories: Categories to prioritize (e.g., ['AST', 'FG%'])
        plays_today: If True, only return players who play today
    
    Returns:
        List of candidates with full context, sorted by streaming value
    """
    target_categories = target_categories or ['AST', 'FG%', 'REB']
    
    # Teams playing today (Monday Jan 5)
    TODAY_TEAMS = {'DET', 'UTA', 'CHA', 'ATL', 'HOU', 'MIN', 'OKC', 'PHO', 'TOR'}
    
    # Teams with 4 games this week
    FOUR_GAME_TEAMS = {'DET', 'UTA', 'CHA', 'DEN', 'MEM', 'BOS', 'POR', 'ATL', 'HOU', 'MIN', 'OKC', 'PHO', 'TOR'}
    
    # Known injury situations (opportunity plays)
    OPPORTUNITY_NOTES = {
        'Paul Reed': 'Jalen Duren injured - gets 30+ min',
        'Ausar Thompson': 'Starting wing when healthy',
        'Moussa Diabaté': 'Starting C for CHA',
        'Collin Sexton': 'High usage guard',
        'Kyle Filipowski': 'Rotation big',
        'Jock Landale': 'Backup C, solid minutes',
        'Bruce Brown': 'Rotation wing/guard',
        'Robert Williams III': 'Elite blocks when healthy',
        'Toumani Camara': 'Starting wing for POR',
        'Peyton Watson': 'Athletic wing, improving',
    }
    
    # Candidates to check
    candidates_to_check = [
        # DET - 4 games, Duran out
        'Paul Reed', 'Ausar Thompson', 'Tim Hardaway Jr.', 'Malik Beasley',
        # UTA - 4 games
        'Kyle Filipowski', 'Brice Sensabaugh', 'Collin Sexton', 'John Collins',
        # CHA - 4 games  
        'Moussa Diabaté', 'Tre Mann', 'Grant Williams', 'Nick Richards',
        # DEN - 4 games
        'Bruce Brown', 'Peyton Watson', 'Julian Strawther',
        # MEM - 4 games
        'Jock Landale', 'Jake LaRavia', 'Scotty Pippen Jr.',
        # POR - 4 games
        'Robert Williams III', 'Toumani Camara', 'Shaedon Sharpe',
        # ATL - 4 games
        'Jalen Johnson', 'De\'Andre Hunter', 'Zaccharie Risacher',
        # Other 4-game teams
        'Jabari Smith Jr.', 'Alperen Sengun', 'Amen Thompson',  # HOU
        'Julius Randle', 'Donte DiVincenzo', 'Naz Reid',  # MIN
    ]
    
    results = []
    
    for name in candidates_to_check:
        context = get_player_context(oauth, name)
        if not context:
            continue
        
        team = context['team']
        
        # Skip if injured
        if context['status'] in ['INJ', 'O', 'IR']:
            continue
        
        # Skip if already owned
        if context['is_owned']:
            continue
        
        # Filter by plays_today if requested
        if plays_today and team.upper() not in TODAY_TEAMS:
            continue
        
        # Only include 4-game teams
        if team.upper() not in FOUR_GAME_TEAMS:
            continue
        
        # Calculate streaming score
        w = context['weighted_per_game']
        
        # Base score from key stats
        score = 0
        if 'AST' in target_categories:
            score += w.get('AST', 0) * 3  # AST weighted 3x
        if 'FG%' in target_categories:
            score += w.get('FG%', 0) * 200  # FG% weighted 200x
        if 'REB' in target_categories:
            score += w.get('REB', 0) * 1
        if 'BLK' in target_categories:
            score += w.get('BLK', 0) * 2
        if 'STL' in target_categories or 'ST' in target_categories:
            score += w.get('ST', 0) * 2
        
        # Add points contribution
        score += w.get('PTS', 0) * 0.5
        
        # Boost for trending up
        score *= context['trend_multiplier']
        
        # Boost for 4-game week
        games_this_week = WEEK_SCHEDULE.get(team.upper(), 3)
        score *= (games_this_week / 3)
        
        context['streaming_score'] = score
        context['games_this_week'] = games_this_week
        context['opportunity_note'] = OPPORTUNITY_NOTES.get(name, '')
        context['plays_today'] = team.upper() in TODAY_TEAMS
        
        results.append(context)
    
    # Sort by streaming score
    results.sort(key=lambda x: x['streaming_score'], reverse=True)
    
    return results


def print_streaming_candidates(candidates: List[Dict], title: str = "STREAMING CANDIDATES"):
    """Print formatted streaming candidates."""
    print()
    print('=' * 110)
    print(f"🎯 {title}")
    print('=' * 110)
    print()
    
    print(f"{'#':<3} {'NAME':<22} {'TEAM':<4} {'G':>2} {'TREND':<10} {'LAST 7D':^30} {'SCORE':>7}")
    print(f"{'':3} {'':22} {'':4} {'':>2} {'':10} {'PTS':>6} {'REB':>5} {'AST':>5} {'FG%':>7} {'':7}")
    print('-' * 110)
    
    for i, c in enumerate(candidates[:15], 1):
        r = c['recent_stats']
        trend = c['trend']
        note = c.get('opportunity_note', '')
        today_mark = '📅' if c.get('plays_today') else '  '
        status = f"({c['status']})" if c['status'] else ""
        
        print(f"{i:<3} {c['name']:<22} {c['team']:<4} {c['games_this_week']:>2} {trend:<10} "
              f"{r['PTS']:>6.0f} {r['REB']:>5.0f} {r['AST']:>5.0f} {r['FG%']:>7.3f} {c['streaming_score']:>7.1f} {today_mark} {status}")
        
        if note:
            print(f"    💡 {note}")
    
    print()
    print("📅 = Plays today")


# ============================================================================
# PLAYER & ROSTER DATA
# ============================================================================

def get_team_roster(oauth: OAuth2, team_key: str) -> List[Dict]:
    """Get full roster for a team."""
    lg = League(oauth, LEAGUE_KEY)
    team = lg.to_team(team_key)
    return team.roster()


def get_player_stats(oauth: OAuth2, player_keys: List[str]) -> Dict[str, Dict]:
    """Get season stats for multiple players."""
    if not player_keys:
        return {}
    
    keys_str = ','.join(player_keys)
    url = f'https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}/players;player_keys={keys_str}/stats?format=json'
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
        status = ''
        
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
                if 'status' in item:
                    status = item['status']
        
        stats = {}
        player_stats = player[1].get('player_stats', {}).get('stats', [])
        for stat in player_stats:
            stat_data = stat.get('stat', {})
            stat_id = str(stat_data.get('stat_id', ''))
            value = stat_data.get('value', '')
            if stat_id in STAT_MAP and value not in ['-', '']:
                try:
                    # Handle percentage values like ".451"
                    if stat_id in ['5', '8']:  # FG%, FT%
                        stats[STAT_MAP[stat_id]] = float(value)
                    else:
                        stats[STAT_MAP[stat_id]] = float(value)
                except:
                    pass
        
        results[player_key] = {
            'name': name,
            'position': position,
            'nba_team': team_abbr,
            'status': status,
            'stats': stats
        }
    
    return results


def filter_active_players(roster: List[Dict]) -> List[Dict]:
    """
    Filter roster to include active + bench players, exclude IL/IL+/IR.
    """
    active = []
    for p in roster:
        pos = p.get('selected_position', '')
        status = p.get('status', '')
        
        # Skip players on IL, IL+, or IR
        if pos in ['IL', 'IL+', 'IR']:
            continue
        
        # Skip players marked as OUT (O) or Injured (INJ) without IL slot
        # But include GTD (game-time decision) players
        if status == 'O':
            continue
            
        active.append(p)
    
    return active


# ============================================================================
# RECENT STATS & LIVE SCORE HELPERS
# ============================================================================

def fetch_live_week_score(oauth: OAuth2, team_key: str, week: int) -> Dict[str, float]:
    """Fetch currently banked category totals for a team this week."""
    url = (f'https://fantasysports.yahooapis.com/fantasy/v2/team/'
           f'{team_key}/stats;type=week;week={week}?format=json')
    try:
        data = api_get(oauth, url)
        stats_list = data['fantasy_content']['team'][1].get('team_stats', {}).get('stats', [])
        result = {}
        for s in stats_list:
            sd = s.get('stat', {})
            sid = str(sd.get('stat_id', ''))
            if sid in STAT_MAP:
                result[STAT_MAP[sid]] = safe_float(sd.get('value', 0))
        return result
    except Exception:
        return {}


def get_player_stats_typed(oauth: OAuth2, player_keys: List[str], stat_type: str) -> Dict[str, Dict]:
    """Fetch stats for player keys with a given Yahoo stat type (lastweek, lastmonth, season)."""
    if not player_keys:
        return {}
    results = {}
    for i in range(0, len(player_keys), 25):
        chunk = player_keys[i:i + 25]
        keys_str = ','.join(chunk)
        url = (f'https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}'
               f'/players;player_keys={keys_str}/stats;type={stat_type}?format=json')
        try:
            data = api_get(oauth, url)
            players = data['fantasy_content']['league'][1]['players']
            for k, v in players.items():
                if k == 'count':
                    continue
                p = v['player']
                player_key = nba_team = name = ''
                for item in p[0]:
                    if not isinstance(item, dict):
                        continue
                    if 'player_key' in item:
                        player_key = item['player_key']
                    if 'name' in item:
                        name = item['name']['full']
                    if 'editorial_team_abbr' in item:
                        nba_team = normalize_team(item['editorial_team_abbr'])
                stats = {}
                for s in p[1].get('player_stats', {}).get('stats', []):
                    sd = s.get('stat', {})
                    sid = str(sd.get('stat_id', ''))
                    if sid in STAT_MAP:
                        stats[STAT_MAP[sid]] = safe_float(sd.get('value', 0))
                if player_key:
                    results[player_key] = {'name': name, 'nba_team': nba_team, 'stats': stats}
        except Exception:
            pass
    return results


def blend_recent_per_game(
    week_stats: Dict[str, float],
    month_stats: Dict[str, float],
    week_games: float,
    month_games: float,
) -> Dict[str, float]:
    """Blend lastweek and lastmonth totals into per-game rates (65% week / 35% month)."""
    week_games = max(week_games, 1.0)
    month_games = max(month_games, 1.0)
    has_week = bool(week_stats)
    has_month = bool(month_stats)
    if has_week and has_month:
        ww, mw = 0.65, 0.35
    elif has_week:
        ww, mw = 1.0, 0.0
    else:
        ww, mw = 0.0, 1.0
    out: Dict[str, float] = {}
    for cat in ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO']:
        w_pg = safe_float(week_stats.get(cat, 0)) / week_games if has_week else 0.0
        m_pg = safe_float(month_stats.get(cat, 0)) / month_games if has_month else 0.0
        out[cat] = w_pg * ww + m_pg * mw
    for cat in ['FG%', 'FT%']:
        wv = safe_float(week_stats.get(cat, 0)) if has_week else 0.0
        mv = safe_float(month_stats.get(cat, 0)) if has_month else 0.0
        if has_week and has_month and wv > 0 and mv > 0:
            out[cat] = wv * ww + mv * mw
        elif wv > 0:
            out[cat] = wv
        else:
            out[cat] = mv
    return out


# ============================================================================
# WEEKLY PROJECTIONS
# ============================================================================

def calculate_weekly_projections(
    players_data: Dict[str, Dict],
    roster: List[Dict],
    exclude_names: List[str] = None,
    add_players: List[Tuple[str, str, Dict]] = None,
    schedule: Dict[str, int] = None,
    per_game_override: Dict[str, Dict[str, float]] = None,
) -> Tuple[Dict[str, float], List[Dict]]:
    """
    Calculate weekly projections for a roster.

    Args:
        players_data: Dict of player_key -> player info with stats
        roster: List of roster entries
        exclude_names: Players to exclude (drops)
        add_players: List of (name, team, stats_dict) to add (streaming adds)
        schedule: Games-per-team dict (defaults to WEEK_SCHEDULE)
        per_game_override: If provided, use these pre-computed per-game rates
                           instead of season totals / GAMES_PLAYED.

    Returns:
        (totals_dict, details_list)
    """
    exclude_names = exclude_names or []
    add_players = add_players or []
    active_schedule = schedule or WEEK_SCHEDULE

    totals = {
        'PTS': 0, 'REB': 0, 'AST': 0, 'ST': 0, 'BLK': 0, '3PTM': 0, 'TO': 0,
        'FGM': 0, 'FGA': 0, 'FTM': 0, 'FTA': 0
    }
    details = []

    # Process roster players
    for p in roster:
        name = p['name']
        if name in exclude_names:
            continue

        player_key = f"466.p.{p['player_id']}"
        if player_key not in players_data:
            continue

        pdata = players_data[player_key]
        stats = pdata['stats']
        team = normalize_team(pdata['nba_team'])
        games = active_schedule.get(team, 3)

        if not stats or stats.get('PTS', 0) == 0:
            continue

        # Use recent per-game rates if available, otherwise fall back to season avg
        if per_game_override and player_key in per_game_override:
            pg = per_game_override[player_key]
            pts_pg = pg.get('PTS', 0)
            reb_pg = pg.get('REB', 0)
            ast_pg = pg.get('AST', 0)
            stl_pg = pg.get('ST', 0)
            blk_pg = pg.get('BLK', 0)
            tpm_pg = pg.get('3PTM', 0)
            to_pg  = pg.get('TO', 0)
            fg_pct = pg.get('FG%', stats.get('FG%', 0.45))
            ft_pct = pg.get('FT%', stats.get('FT%', 0.75))
        else:
            pts_pg = stats.get('PTS', 0) / GAMES_PLAYED
            reb_pg = stats.get('REB', 0) / GAMES_PLAYED
            ast_pg = stats.get('AST', 0) / GAMES_PLAYED
            stl_pg = stats.get('ST', 0) / GAMES_PLAYED
            blk_pg = stats.get('BLK', 0) / GAMES_PLAYED
            tpm_pg = stats.get('3PTM', 0) / GAMES_PLAYED
            to_pg  = stats.get('TO', 0) / GAMES_PLAYED
            fg_pct = stats.get('FG%', 0.45)
            ft_pct = stats.get('FT%', 0.75)
        
        # Weekly projections
        totals['PTS'] += pts_pg * games
        totals['REB'] += reb_pg * games
        totals['AST'] += ast_pg * games
        totals['ST'] += stl_pg * games
        totals['BLK'] += blk_pg * games
        totals['3PTM'] += tpm_pg * games
        totals['TO'] += to_pg * games
        
        # FG%/FT% volume estimates
        fga_pg = (pts_pg * 0.6) / 2 / max(fg_pct, 0.3) if fg_pct > 0 else 0
        fgm_pg = fga_pg * fg_pct
        fta_pg = (pts_pg * 0.15) / max(ft_pct, 0.3) if ft_pct > 0 else 0
        ftm_pg = fta_pg * ft_pct
        
        totals['FGM'] += fgm_pg * games
        totals['FGA'] += fga_pg * games
        totals['FTM'] += ftm_pg * games
        totals['FTA'] += fta_pg * games
        
        details.append({
            'name': name,
            'team': team,
            'games': games,
            'pts': pts_pg * games,
            'reb': reb_pg * games,
            'ast': ast_pg * games,
            'stl': stl_pg * games,
            'blk': blk_pg * games,
            '3pm': tpm_pg * games,
            'to': to_pg * games,
            'fg': fg_pct,
            'ft': ft_pct,
            'streamer': False
        })
    
    # Add streaming players
    for add_name, add_team, add_stats in add_players:
        games = active_schedule.get(normalize_team(add_team), 3)
        
        pts_pg = add_stats.get('PTS', 0) / GAMES_PLAYED
        reb_pg = add_stats.get('REB', 0) / GAMES_PLAYED
        ast_pg = add_stats.get('AST', 0) / GAMES_PLAYED
        stl_pg = add_stats.get('ST', 0) / GAMES_PLAYED
        blk_pg = add_stats.get('BLK', 0) / GAMES_PLAYED
        tpm_pg = add_stats.get('3PTM', 0) / GAMES_PLAYED
        to_pg = add_stats.get('TO', 0) / GAMES_PLAYED
        
        fg_pct = add_stats.get('FG%', 0.45)
        ft_pct = add_stats.get('FT%', 0.75)
        
        totals['PTS'] += pts_pg * games
        totals['REB'] += reb_pg * games
        totals['AST'] += ast_pg * games
        totals['ST'] += stl_pg * games
        totals['BLK'] += blk_pg * games
        totals['3PTM'] += tpm_pg * games
        totals['TO'] += to_pg * games
        
        fga_pg = (pts_pg * 0.6) / 2 / max(fg_pct, 0.3) if fg_pct > 0 else 0
        fgm_pg = fga_pg * fg_pct
        fta_pg = (pts_pg * 0.15) / max(ft_pct, 0.3) if ft_pct > 0 else 0
        ftm_pg = fta_pg * ft_pct
        
        totals['FGM'] += fgm_pg * games
        totals['FGA'] += fga_pg * games
        totals['FTM'] += ftm_pg * games
        totals['FTA'] += fta_pg * games
        
        details.append({
            'name': add_name,
            'team': add_team,
            'games': games,
            'pts': pts_pg * games,
            'reb': reb_pg * games,
            'ast': ast_pg * games,
            'stl': stl_pg * games,
            'blk': blk_pg * games,
            '3pm': tpm_pg * games,
            'to': to_pg * games,
            'fg': fg_pct,
            'ft': ft_pct,
            'streamer': True
        })
    
    # Calculate percentages
    totals['FG%'] = totals['FGM'] / totals['FGA'] if totals['FGA'] > 0 else 0
    totals['FT%'] = totals['FTM'] / totals['FTA'] if totals['FTA'] > 0 else 0
    
    return totals, details


# ============================================================================
# MATCHUP ANALYSIS
# ============================================================================

def analyze_categories(my_totals: Dict, opp_totals: Dict, punt_cats: List[str] = None) -> Dict:
    """Analyze category-by-category comparison."""
    punt_cats = punt_cats or []
    analysis = {}
    
    for cat in CATEGORIES + ['TO']:
        my_val = my_totals.get(cat, 0)
        opp_val = opp_totals.get(cat, 0)
        
        if cat == 'TO':
            # Lower is better for TO
            diff = opp_val - my_val
            winner = 'YOU' if my_val < opp_val else 'OPP' if opp_val < my_val else 'TIE'
        else:
            diff = my_val - opp_val
            winner = 'YOU' if my_val > opp_val else 'OPP' if opp_val > my_val else 'TIE'
        
        is_punt = cat in punt_cats
        
        analysis[cat] = {
            'my_value': my_val,
            'opp_value': opp_val,
            'difference': diff,
            'winner': winner,
            'is_punt': is_punt
        }
    
    return analysis


def count_wins(analysis: Dict, punt_cats: List[str] = None) -> Tuple[int, int]:
    """Count projected wins/losses."""
    punt_cats = punt_cats or []
    wins = 0
    losses = 0
    
    for cat, data in analysis.items():
        if cat in punt_cats:
            continue
        if data['winner'] == 'YOU':
            wins += 1
        elif data['winner'] == 'OPP':
            losses += 1
    
    return wins, losses


# ============================================================================
# DISPLAY FUNCTIONS
# ============================================================================

def print_header(title: str):
    """Print section header."""
    print()
    print('=' * 100)
    print(f"🏀 {title}")
    print('=' * 100)


def print_roster(details: List[Dict], title: str, show_streamer: bool = False):
    """Print roster details."""
    print_header(title)
    print(f"{'Player':<25} {'Team':<4} {'G':>2} {'PTS':>7} {'REB':>6} {'AST':>6} {'STL':>5} {'BLK':>5} {'3PM':>5} {'FG%':>6} {'FT%':>6}")
    print('-' * 100)
    
    details_sorted = sorted(details, key=lambda x: x['pts'], reverse=True)
    for p in details_sorted:
        stream_mark = ' *' if show_streamer and p.get('streamer') else ''
        print(f"{p['name'][:24]:<25} {p['team']:<4} {p['games']:>2} "
              f"{p['pts']:>7.1f} {p['reb']:>6.1f} {p['ast']:>6.1f} "
              f"{p['stl']:>5.1f} {p['blk']:>5.1f} {p['3pm']:>5.1f} "
              f"{p['fg']:>6.3f} {p['ft']:>6.3f}{stream_mark}")
    
    print('-' * 100)


def print_category_comparison(my_totals: Dict, opp_totals: Dict, analysis: Dict, 
                               before_totals: Dict = None, punt_cats: List[str] = None):
    """Print category comparison."""
    punt_cats = punt_cats or []
    
    print_header("CATEGORY-BY-CATEGORY COMPARISON")
    
    if before_totals:
        print(f"\n{'Category':<8} {'BEFORE':>10} {'AFTER':>10} {'CHANGE':>10} {'OPPONENT':>10} {'WINNER':>12} {'MARGIN':>10}")
    else:
        print(f"\n{'Category':<8} {'YOU':>10} {'OPPONENT':>10} {'WINNER':>12} {'MARGIN':>10}")
    print('-' * 80)
    
    for cat in CATEGORIES + ['TO']:
        my_val = my_totals.get(cat, 0)
        opp_val = opp_totals.get(cat, 0)
        data = analysis[cat]
        
        is_punt = cat in punt_cats
        winner_str = '(PUNT)' if is_punt else data['winner']
        margin = data['difference']
        
        if before_totals:
            before_val = before_totals.get(cat, 0)
            change = my_val - before_val
            
            if cat in ['FG%', 'FT%']:
                print(f"{cat:<8} {before_val:>10.3f} {my_val:>10.3f} {change:>+10.3f} "
                      f"{opp_val:>10.3f} {winner_str:>12} {margin:>+10.3f}")
            else:
                print(f"{cat:<8} {before_val:>10.1f} {my_val:>10.1f} {change:>+10.1f} "
                      f"{opp_val:>10.1f} {winner_str:>12} {margin:>+10.1f}")
        else:
            if cat in ['FG%', 'FT%']:
                print(f"{cat:<8} {my_val:>10.3f} {opp_val:>10.3f} {winner_str:>12} {margin:>+10.3f}")
            else:
                print(f"{cat:<8} {my_val:>10.1f} {opp_val:>10.1f} {winner_str:>12} {margin:>+10.1f}")
    
    print('-' * 80)


def print_projection(analysis: Dict, punt_cats: List[str] = None, label: str = ""):
    """Print win projection."""
    wins, losses = count_wins(analysis, punt_cats)
    total_cats = len(CATEGORIES)
    punt_str = f" (punting {', '.join(punt_cats)})" if punt_cats else ""
    
    print(f"\n📊 PROJECTION{' ' + label if label else ''}: {wins}-{losses}{punt_str}")


# ============================================================================
# MAIN ANALYSIS
# ============================================================================

def run_streaming_analysis(
    drop_players: List[str] = None,
    add_players: List[Tuple[str, str, Dict]] = None,
    punt_categories: List[str] = None
):
    """
    Run the full streaming analysis.

    Uses: live banked scores + recent per-game rates (65% last week / 35% last month)
          × remaining games this week. This gives an accurate picture of where
          the matchup actually stands rather than hypothetical season projections.
    """
    drop_players = drop_players or []
    add_players = add_players or []
    punt_categories = punt_categories or PUNT_CATEGORIES

    print("\n" + "=" * 100)
    print("🏀" + " " * 35 + "WEEKLY STREAMING ANALYSIS" + " " * 35 + "🏀")
    print("=" * 100)

    oauth = ensure_oauth()
    lg = League(oauth, LEAGUE_KEY)

    current_week = lg.current_week()
    week_start, week_end = lg.week_date_range(current_week)
    today = datetime.now().date()

    print(f"\n📅 Week {current_week} ({week_start} – {week_end})")
    print(f"⚙️  Projections: live banked score + recent form × remaining games")

    # Fetch REMAINING schedule from today (not full week)
    print("\n📡 Fetching remaining schedule from ESPN...")
    remaining_start = max(today, week_start)
    remaining_schedule = fetch_week_schedule(remaining_start, week_end)
    if remaining_schedule:
        three_plus = sorted([t for t, g in remaining_schedule.items() if g >= 3])
        print(f"   3+ games remaining: {', '.join(three_plus) if three_plus else 'none'}")
    else:
        remaining_schedule = WEEK_SCHEDULE
        print("   ESPN unavailable, using fallback.")

    # Fetch ESPN game counts for recent windows (to normalize per-game rates)
    lookback_end = today - timedelta(days=1)
    week_game_counts = fetch_week_schedule(lookback_end - timedelta(days=6), lookback_end)
    month_game_counts = fetch_week_schedule(lookback_end - timedelta(days=29), lookback_end)

    # Resolve matchup
    my_team = lg.to_team(MY_TEAM_KEY)
    opp_team_key = my_team.matchup(current_week)
    my_team_name, opp_team_name = 'Your Team', 'Opponent'
    try:
        teams = lg.teams()
        for tk, ti in teams.items():
            if tk == MY_TEAM_KEY:
                my_team_name = ti.get('name', 'Your Team')
            elif tk == opp_team_key:
                opp_team_name = ti.get('name', 'Opponent')
    except Exception:
        pass

    print(f"🆚 {my_team_name} vs {opp_team_name}")

    # Live banked scores (games already played this week)
    print("\n📊 Fetching live scores...")
    my_live = fetch_live_week_score(oauth, MY_TEAM_KEY, current_week)
    opp_live = fetch_live_week_score(oauth, opp_team_key, current_week)

    live_cats = ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM']
    print(f"   YOU  (banked): " + "  ".join(f"{c}={my_live.get(c, 0):.0f}" for c in live_cats)
          + f"  FG%={my_live.get('FG%', 0):.3f}  FT%={my_live.get('FT%', 0):.3f}")
    print(f"   OPP  (banked): " + "  ".join(f"{c}={opp_live.get(c, 0):.0f}" for c in live_cats)
          + f"  FG%={opp_live.get('FG%', 0):.3f}  FT%={opp_live.get('FT%', 0):.3f}")

    # Rosters
    print("\n📥 Fetching rosters and recent stats...")
    my_roster_raw = get_team_roster(oauth, MY_TEAM_KEY)
    opp_roster_raw = get_team_roster(oauth, opp_team_key)
    my_roster = filter_active_players(my_roster_raw)
    opp_roster = filter_active_players(opp_roster_raw)
    print(f"   Your active roster: {len(my_roster)} players")
    print(f"   Opponent active roster: {len(opp_roster)} players")

    my_player_keys = [f"466.p.{p['player_id']}" for p in my_roster]
    opp_player_keys = [f"466.p.{p['player_id']}" for p in opp_roster]
    all_keys = list(set(my_player_keys + opp_player_keys))

    # Fetch stats: recent (lastweek + lastmonth) and season as fallback
    week_data = get_player_stats_typed(oauth, all_keys, 'lastweek')
    month_data = get_player_stats_typed(oauth, all_keys, 'lastmonth')
    season_data = get_player_stats(oauth, all_keys)

    # Build nba_team map from all sources
    nba_team_map: Dict[str, str] = {}
    for src in [season_data, month_data, week_data]:
        for pk, d in src.items():
            if pk not in nba_team_map or not nba_team_map[pk]:
                nba_team_map[pk] = normalize_team(d.get('nba_team', ''))

    # Build per-game override: blend recent stats; fall back to season if no recent data
    pg_override: Dict[str, Dict[str, float]] = {}
    for pk in all_keys:
        team = nba_team_map.get(pk, '')
        wg = max(float(week_game_counts.get(team, 3)), 1.0)
        mg = max(float(month_game_counts.get(team, 12)), 1.0)
        ws = week_data.get(pk, {}).get('stats', {})
        ms = month_data.get(pk, {}).get('stats', {})
        ss = season_data.get(pk, {}).get('stats', {})
        if ws or ms:
            pg_override[pk] = blend_recent_per_game(ws, ms, wg, mg)
        elif ss:
            pg_override[pk] = {cat: safe_float(ss.get(cat, 0)) / GAMES_PLAYED
                                for cat in ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO']}
            pg_override[pk]['FG%'] = safe_float(ss.get('FG%', 0))
            pg_override[pk]['FT%'] = safe_float(ss.get('FT%', 0))

    # Project REMAINING games using recent per-game rates
    before_rem, before_details = calculate_weekly_projections(
        season_data, my_roster, schedule=remaining_schedule, per_game_override=pg_override
    )
    after_rem, after_details = calculate_weekly_projections(
        season_data, my_roster,
        exclude_names=drop_players, add_players=add_players,
        schedule=remaining_schedule, per_game_override=pg_override
    )
    opp_rem, opp_details = calculate_weekly_projections(
        season_data, opp_roster, schedule=remaining_schedule, per_game_override=pg_override
    )

    # Combine banked + remaining into final projected totals
    def add_banked(live: Dict, remaining: Dict) -> Dict:
        combined: Dict[str, float] = {}
        for cat in ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO']:
            combined[cat] = safe_float(live.get(cat, 0)) + remaining.get(cat, 0)
        live_pts = safe_float(live.get('PTS', 0))
        live_fg = safe_float(live.get('FG%', 0))
        live_ft = safe_float(live.get('FT%', 0))
        fga_live = (live_pts * 0.55) / max(live_fg, 0.25) if live_pts > 0 and live_fg > 0 else 0.0
        fta_live = (live_pts * 0.18) / max(live_ft, 0.40) if live_pts > 0 and live_ft > 0 else 0.0
        fgm = fga_live * live_fg + remaining.get('FGM', 0)
        fga = fga_live + remaining.get('FGA', 0)
        ftm = fta_live * live_ft + remaining.get('FTM', 0)
        fta = fta_live + remaining.get('FTA', 0)
        combined['FG%'] = fgm / fga if fga > 0 else 0.0
        combined['FT%'] = ftm / fta if fta > 0 else 0.0
        return combined

    before_totals = add_banked(my_live, before_rem)
    after_totals  = add_banked(my_live, after_rem)
    opp_totals    = add_banked(opp_live, opp_rem)

    # Analyze
    before_analysis = analyze_categories(before_totals, opp_totals, punt_categories)
    after_analysis  = analyze_categories(after_totals,  opp_totals, punt_categories)

    # Print rosters (remaining projections only)
    print_roster(before_details,
                 f"YOUR TEAM — REMAINING PROJECTION ({len(before_details)} active players, recent form)")
    totals = before_rem
    print(f"{'REM. PROJ':<25} {'':<4} {'':<2} "
          f"{totals['PTS']:>7.1f} {totals['REB']:>6.1f} {totals['AST']:>6.1f} "
          f"{totals['ST']:>5.1f} {totals['BLK']:>5.1f} {totals['3PTM']:>5.1f} "
          f"{totals['FG%']:>6.3f} {totals['FT%']:>6.3f}")

    if drop_players or add_players:
        print_roster(after_details, "YOUR TEAM — AFTER STREAMING", show_streamer=True)
        totals = after_rem
        print(f"{'REM. PROJ':<25} {'':<4} {'':<2} "
              f"{totals['PTS']:>7.1f} {totals['REB']:>6.1f} {totals['AST']:>6.1f} "
              f"{totals['ST']:>5.1f} {totals['BLK']:>5.1f} {totals['3PTM']:>5.1f} "
              f"{totals['FG%']:>6.3f} {totals['FT%']:>6.3f}")
        print("* = STREAMING PICKUP")

    print_roster(opp_details,
                 f"OPPONENT — {opp_team_name} ({len(opp_details)} active players, recent form)")
    totals = opp_rem
    print(f"{'REM. PROJ':<25} {'':<4} {'':<2} "
          f"{totals['PTS']:>7.1f} {totals['REB']:>6.1f} {totals['AST']:>6.1f} "
          f"{totals['ST']:>5.1f} {totals['BLK']:>5.1f} {totals['3PTM']:>5.1f} "
          f"{totals['FG%']:>6.3f} {totals['FT%']:>6.3f}")

    # Final projected totals (banked + remaining)
    if drop_players or add_players:
        print_category_comparison(after_totals, opp_totals, after_analysis,
                                  before_totals, punt_categories)
        print_projection(before_analysis, punt_categories, "BEFORE streaming")
        print_projection(after_analysis,  punt_categories, "AFTER streaming")
    else:
        print_category_comparison(before_totals, opp_totals, before_analysis,
                                  punt_cats=punt_categories)
        print_projection(before_analysis, punt_categories)

    # Close categories
    final_analysis = after_analysis if (drop_players or add_players) else before_analysis
    print_header("CLOSE CATEGORIES (margin < 15%)")
    for cat in CATEGORIES:
        data = final_analysis[cat]
        if data.get('is_punt'):
            continue
        my_val  = data['my_value']
        opp_val = data['opp_value']
        margin  = data['difference']
        pct = abs(margin / opp_val * 100) if opp_val > 0 else 0
        if pct < 15:
            if cat in ['FG%', 'FT%']:
                print(f"   {cat}: YOU {my_val:.3f} vs OPP {opp_val:.3f} (margin: {margin:+.3f})")
            else:
                print(f"   {cat}: YOU {my_val:.1f} vs OPP {opp_val:.1f} "
                      f"(margin: {margin:+.1f}, {pct:.1f}%)")

    print("\n" + "=" * 100)
    print("🏀" + " " * 35 + "END OF ANALYSIS" + " " * 35 + "🏀")
    print("=" * 100 + "\n")

    return {
        'before_totals': before_totals, 'after_totals': after_totals,
        'opp_totals': opp_totals, 'before_analysis': before_analysis,
        'after_analysis': after_analysis, 'my_live': my_live, 'opp_live': opp_live,
    }


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    print(f"\n📅 Analysis Date: {datetime.now().strftime('%A, %B %d, %Y')}")
    # Run full matchup + streaming analysis using live Yahoo rosters and ESPN schedule
    run_streaming_analysis(punt_categories=['TO'])
