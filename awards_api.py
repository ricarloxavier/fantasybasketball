#!/usr/bin/env python3
"""
🏆 PUTILLA AWARDS API MODULE 🏆

This module wraps the original putilla_awards_v2.py script to provide a clean
interface for the web application. It calculates all awards without modifying
the original script's logic.

KEY CONCEPT - What is a "Putilla"?
A "putilla" is a player who gets dropped multiple times in the league waiver wire.
These are the players who teams pick up hoping for value, but quickly give up on.
This script tracks which players got dropped most (TRUE PUTILLAS = 2+ drops)
and awards teams/players for their putilla behavior.

Awards Calculated:
TEAM AWARDS (6):
  - Putilla Suprema: Team with most games started by putillas
  - Putilla de Plata: 2nd place in games started
  - Putilla de Bronce: 3rd place in games started
  - Putilla Más Activa: Team with most unique putilla players
  - Putilla Fiel: Team with fewest unique putillas (most loyal)
  - El Putilla Housewife: Team with most players added but never dropped

PLAYER AWARDS (3):
  - La Putilla Escondida: Best value per game (hidden gem)
  - Putilla MVP: Highest total value across season
  - Putilla Basura: Worst value per game (trash pickup)
"""

import os
import sys
import io
from contextlib import redirect_stdout
from datetime import datetime
from typing import Dict, List

# Import functions from the original putilla_awards_v2.py script
# That script handles all Yahoo Fantasy API interactions and data processing
from putilla_awards_v2 import (
    ensure_oauth,            # Handles Yahoo OAuth authentication
    fetch_all_transactions,  # Gets all waiver wire moves from Yahoo API
    build_roster_timeline,   # Processes transactions into player "stints" on teams
    analyze_roster_stints,   # Calculates fantasy value for each stint
    get_current_week         # Gets current week number from Yahoo API
)

# Hard-coded league identifier for "Chantasy 2025-26" league
# Format: {game_id}.l.{league_id}
# 466 = NBA 2025-26 season
# 10145 = Your specific league ID
LEAGUE_KEY = '466.l.10145'


# ============================================================================
# DATA FETCHING FUNCTIONS
# ============================================================================

def get_all_teams_rosters(oauth):
    """
    Fetch and process all roster data from Yahoo Fantasy API.
    
    This function:
    1. Fetches all waiver transactions (adds/drops) from Yahoo API
    2. Builds a timeline of which players were on which teams and when
    3. Counts how many times each player was dropped
    
    Args:
        oauth: Authenticated Yahoo OAuth2 session object
    
    Returns:
        dict: {
            'roster_stints': {
                'Player Name': [
                    {
                        'team_name': 'Team Name',
                        'start_week': 5,
                        'end_week': 8,
                        'weeks': 3,
                        ...
                    },
                    ...
                ],
                ...
            },
            'drop_counts': {
                'Player Name': 3,  # Number of times dropped
                ...
            }
        }
    """
    # Fetch all waiver wire transactions (adds/drops) from Yahoo API
    transactions = fetch_all_transactions(oauth, LEAGUE_KEY)
    
    # Process transactions into roster "stints" (periods a player was on a team)
    # Also count how many times each player was dropped
    roster_stints, drop_counts = build_roster_timeline(transactions)
    
    return {'roster_stints': roster_stints, 'drop_counts': drop_counts}


# ============================================================================
# AWARDS CALCULATION - MAIN FUNCTION
# ============================================================================

def calculate_awards(rosters_data: Dict) -> Dict:
    """
    Calculate all Putilla Awards from roster data.
    
    This is the main orchestration function that:
    1. Filters to only TRUE PUTILLAS (players dropped 2+ times)
    2. Calculates fantasy value for each player stint
    3. Calculates all 8 awards (5 team + 3 player)
    4. Returns structured data for web display
    
    FILTERING LOGIC:
    We only award TRUE PUTILLAS - players dropped at least twice.
    Why? Single drops might be legitimate moves (injury, schedule, etc).
    Multiple drops = a pattern of failed pickups = true putilla behavior.
    
    Args:
        rosters_data: Dict containing 'roster_stints' and 'drop_counts'
                      from get_all_teams_rosters()
    
    Returns:
        dict: {
            'team_awards': {
                'putilla_suprema': {...},
                'putilla_de_plata': {...},
                ...
            },
            'player_awards': {
                'la_putilla_escondida': {...},
                'putilla_MVP': {...},
                'putilla_basura': {...}
            },
            'total_stints': 156,  # Total number of putilla stints analyzed
            'current_week': 12    # Current week of NBA season
        }
    """
    # Authenticate with Yahoo API (needed to fetch player stats)
    oauth = ensure_oauth(os.environ.get('YAHOO_OAUTH_FILE', 'oauth2.json'))
    
    # Get current week number from Yahoo API (season is ~24 weeks long)
    current_week = get_current_week(oauth, LEAGUE_KEY)
    
    # ========================================================================
    # STEP 1: FILTER TO TRUE PUTILLAS (dropped 2+ times)
    # ========================================================================
    
    min_drops = 2  # Threshold: must be dropped at least twice to qualify
    drop_counts = rosters_data.get('drop_counts', {})
    
    # Filter roster_stints dict to only include players dropped 2+ times
    # This is the "TRUE PUTILLAS" filter
    putilla_stints = {
        player: stints 
        for player, stints in rosters_data['roster_stints'].items()
        if drop_counts.get(player, 0) >= min_drops  # Only players with 2+ drops
    }
    
    # ========================================================================
    # STEP 2: CALCULATE FANTASY VALUE FOR EACH STINT
    # ========================================================================
    
    # analyze_roster_stints() fetches stats from Yahoo API and calculates
    # fantasy point value for each stint (period player was on a team)
    # Returns list of stint dicts with 'total_value', 'weeks', etc.
    stint_values = analyze_roster_stints(
        oauth, 
        LEAGUE_KEY, 
        putilla_stints,  # Filtered to only TRUE PUTILLAS
        current_week,
        drop_counts
    )
    
    # ========================================================================
    # STEP 3: ADD VALUE PER GAME METRIC
    # ========================================================================
    
    # Calculate value_per_game for each stint
    # This is important for awards like "La Putilla Escondida" (best per-game value)
    # and "Putilla Basura" (worst per-game value)
    for stint in stint_values:
        if stint['weeks'] > 0:
            # Value per game = total fantasy points / weeks played
            stint['value_per_game'] = stint['total_value'] / stint['weeks']
        else:
            stint['value_per_game'] = 0  # Avoid division by zero
    
    # ========================================================================
    # STEP 4: CALCULATE AWARDS
    # ========================================================================
    
    # Dictionary to store all award results
    awards_result = {}
    
    # ========================================================================
    # TEAM AWARD #1: PUTILLA SUPREMA
    # Team that started the most games with putilla players
    # ========================================================================
    
    # Sum up total weeks (games) each team started putillas
    # Also track which players contributed
    games_by_team = {}
    players_by_team_suprema = {}
    for stint in stint_values:
        team = stint['team_name']
        games = stint['weeks']  # Number of weeks this player was on this team
        games_by_team[team] = games_by_team.get(team, 0) + games
        
        # Track unique players per team
        if team not in players_by_team_suprema:
            players_by_team_suprema[team] = set()
        players_by_team_suprema[team].add(stint['player_name'])
    
    # Sort teams by total games started (highest first)
    sorted_teams = sorted(games_by_team.items(), key=lambda x: x[1], reverse=True)
    
    # Award goes to team with most games started
    awards_result['putilla_suprema'] = {
        'winner': {
            'team_name': sorted_teams[0][0],  # Team name
            'player_name': 'Team Total',       # Not a specific player
            'games_started': sorted_teams[0][1],  # Total weeks
            'player_list': sorted(list(players_by_team_suprema[sorted_teams[0][0]]))
        },
        'runners_up': [
            {
                'team_name': t, 
                'player_name': 'Team Total', 
                'games_started': g,
                'player_list': sorted(list(players_by_team_suprema[t]))
            }
            for t, g in sorted_teams[1:4]  # Show top 3 runners-up
        ]
    }
    
    # ========================================================================
    # TEAM AWARD #2: PUTILLA DE PLATA (Silver)
    # 2nd place in games started
    # ========================================================================
    
    if len(sorted_teams) > 1:
        awards_result['putilla_de_plata'] = {
            'winner': {
                'team_name': sorted_teams[1][0],
                'player_name': 'Team Total',
                'games_started': sorted_teams[1][1],
                'player_list': sorted(list(players_by_team_suprema[sorted_teams[1][0]]))
            },
            'runners_up': []  # No runners-up for 2nd place award
        }
    
    # ========================================================================
    # TEAM AWARD #3: PUTILLA DE BRONCE (Bronze)
    # 3rd place in games started
    # ========================================================================
    
    if len(sorted_teams) > 2:
        awards_result['putilla_de_bronce'] = {
            'winner': {
                'team_name': sorted_teams[2][0],
                'player_name': 'Team Total',
                'games_started': sorted_teams[2][1],
                'player_list': sorted(list(players_by_team_suprema[sorted_teams[2][0]]))
            },
            'runners_up': []
        }
    
    # ========================================================================
    # TEAM AWARD #4: PUTILLA MÁS ACTIVA
    # Team that rostered the most UNIQUE putilla players
    # (Most churning of putillas)
    # ========================================================================
    
    # Count unique putilla players per team
    unique_putillas_by_team = {}
    for stint in stint_values:
        team = stint['team_name']
        if team not in unique_putillas_by_team:
            unique_putillas_by_team[team] = set()  # Use set to track unique players
        unique_putillas_by_team[team].add(stint['player_name'])
    
    # Convert sets to counts
    unique_counts = {team: len(players) for team, players in unique_putillas_by_team.items()}
    sorted_unique = sorted(unique_counts.items(), key=lambda x: x[1], reverse=True)
    
    awards_result['putilla_mas_activa'] = {
        'winner': {
            'team_name': sorted_unique[0][0],
            'player_name': 'Team Total',
            'unique_players': sorted_unique[0][1],  # Count of unique putillas
            'player_list': sorted(list(unique_putillas_by_team[sorted_unique[0][0]]))
        },
        'runners_up': [
            {
                'team_name': t, 
                'player_name': 'Team Total', 
                'unique_players': u,
                'player_list': sorted(list(unique_putillas_by_team[t]))
            }
            for t, u in sorted_unique[1:4]
        ]
    }
    
    # ========================================================================
    # TEAM AWARD #5: PUTILLA FIEL (Faithful)
    # Team with FEWEST unique putillas (most loyal to their bad picks)
    # ========================================================================
    
    sorted_faithful = sorted(unique_counts.items(), key=lambda x: x[1])  # Ascending
    
    awards_result['putilla_fiel'] = {
        'winner': {
            'team_name': sorted_faithful[0][0],
            'player_name': 'Team Total',
            'unique_players': sorted_faithful[0][1],
            'player_list': sorted(list(unique_putillas_by_team[sorted_faithful[0][0]]))
        },
        'runners_up': [
            {
                'team_name': t, 
                'player_name': 'Team Total', 
                'unique_players': u,
                'player_list': sorted(list(unique_putillas_by_team[t]))
            }
            for t, u in sorted_faithful[1:4]
        ]
    }
    
    # ========================================================================
    # TEAM AWARD #6: EL PUTILLA HOUSEWIFE
    # Team with most players added but NEVER dropped (loyal keepers)
    # These are the players teams believed in enough to never drop
    # NOTE: Deni Avdija DOES count for this award (unlike other putilla awards)
    # ========================================================================
    
    # Get all roster stints (including players dropped 0 or 1 time)
    all_roster_stints = rosters_data['roster_stints']
    
    # Count players added but never dropped (drop_count = 0) per team
    # Also store the actual player names for display
    housewife_players_by_team = {}
    for player, stints in all_roster_stints.items():
        # Only count players who were never dropped (drop_count = 0)
        if drop_counts.get(player, 0) == 0:
            for stint in stints:
                team = stint['team_name']
                if team not in housewife_players_by_team:
                    housewife_players_by_team[team] = set()
                housewife_players_by_team[team].add(player)
    
    # Convert sets to counts and lists
    housewife_counts = {team: len(players) for team, players in housewife_players_by_team.items()}
    sorted_housewife = sorted(housewife_counts.items(), key=lambda x: x[1], reverse=True)
    
    if sorted_housewife:
        winning_team = sorted_housewife[0][0]
        awards_result['el_putilla_housewife'] = {
            'winner': {
                'team_name': winning_team,
                'player_name': 'Team Total',
                'unique_players': sorted_housewife[0][1],
                'player_list': sorted(list(housewife_players_by_team[winning_team]))  # List of players
            },
            'runners_up': [
                {
                    'team_name': t, 
                    'player_name': 'Team Total', 
                    'unique_players': u,
                    'player_list': sorted(list(housewife_players_by_team[t]))  # List for each runner-up
                }
                for t, u in sorted_housewife[1:4] if t in housewife_players_by_team
            ]
        }
    
    # ========================================================================
    # PLAYER AWARD #1: LA PUTILLA ESCONDIDA (Hidden Gem)
    # Putilla player with best value per game (minimum 5 weeks played)
    # This is the "diamond in the rough" - dropped multiple times but actually good
    # ========================================================================
    
    # Only consider stints of at least 5 weeks (avoid small sample sizes)
    eligible_stints = [s for s in stint_values if s['weeks'] >= 5]
    
    # Sort by value_per_game (highest first)
    sorted_value_pg = sorted(eligible_stints, key=lambda x: x['value_per_game'], reverse=True)
    
    if sorted_value_pg:
        awards_result['la_putilla_escondida'] = {
            'winner': {
                'team_name': sorted_value_pg[0]['team_name'],
                'player_name': sorted_value_pg[0]['player_name'],
                'value_per_game': sorted_value_pg[0]['value_per_game'],  # Fantasy points per week
                'games_started': sorted_value_pg[0]['weeks']
            },
            'runners_up': [
                {
                    'team_name': s['team_name'],
                    'player_name': s['player_name'],
                    'value_per_game': s['value_per_game'],
                    'games_started': s['weeks']
                }
                for s in sorted_value_pg[1:4]  # Top 3 runners-up
            ]
        }
    
    # ========================================================================
    # PLAYER AWARD #2: PUTILLA MVP
    # Putilla player with highest TOTAL value (minimum 10 weeks played)
    # This rewards consistency - dropped multiple times but provided most value overall
    # ========================================================================
    
    # Only consider stints of at least 10 weeks (substantial playing time)
    eligible_mvp = [s for s in stint_values if s['weeks'] >= 10]
    
    # Sort by total_value (highest first)
    sorted_total = sorted(eligible_mvp, key=lambda x: x['total_value'], reverse=True)
    
    if sorted_total:
        awards_result['putilla_MVP'] = {
            'winner': {
                'team_name': sorted_total[0]['team_name'],
                'player_name': sorted_total[0]['player_name'],
                'total_value': sorted_total[0]['total_value'],  # Total fantasy points
                'games_started': sorted_total[0]['weeks']
            },
            'runners_up': [
                {
                    'team_name': s['team_name'],
                    'player_name': s['player_name'],
                    'total_value': s['total_value'],
                    'games_started': s['weeks']
                }
                for s in sorted_total[1:4]
            ]
        }
    
    # ========================================================================
    # PLAYER AWARD #3: PUTILLA BASURA (Trash)
    # Putilla player with WORST value per game (minimum 5 weeks)
    # The truly bad pickup - dropped multiple times for good reason
    # 
    # IMPORTANT: Exclude MVP winner to avoid same player winning both awards
    # (A player could have high total value but low per-game if played many weeks)
    # ========================================================================
    
    # Get MVP winner's name to exclude them
    mvp_player = sorted_total[0]['player_name'] if sorted_total else None
    
    # Filter out MVP winner from candidates
    basura_candidates = [s for s in eligible_stints if s['player_name'] != mvp_player]
    
    # Sort by value_per_game (lowest first - this is the worst award!)
    sorted_worst = sorted(basura_candidates, key=lambda x: x['value_per_game'])
    
    if sorted_worst:
        awards_result['putilla_basura'] = {
            'winner': {
                'team_name': sorted_worst[0]['team_name'],
                'player_name': sorted_worst[0]['player_name'],
                'value_per_game': sorted_worst[0]['value_per_game'],  # Lowest value
                'games_started': sorted_worst[0]['weeks']
            },
            'runners_up': [
                {
                    'team_name': s['team_name'],
                    'player_name': s['player_name'],
                    'value_per_game': s['value_per_game'],
                    'games_started': s['weeks']
                }
                for s in sorted_worst[1:4]
            ]
        }
    
    # ========================================================================
    # STEP 5: ORGANIZE AWARDS INTO CATEGORIES
    # ========================================================================
    
    # Separate team awards (based on team totals) from player awards (individual players)
    team_awards = {
        'putilla_suprema': awards_result.get('putilla_suprema'),
        'putilla_de_plata': awards_result.get('putilla_de_plata'),
        'putilla_de_bronce': awards_result.get('putilla_de_bronce'),
        'putilla_mas_activa': awards_result.get('putilla_mas_activa'),
        'putilla_fiel': awards_result.get('putilla_fiel'),
        'el_putilla_housewife': awards_result.get('el_putilla_housewife')
    }
    
    player_awards = {
        'la_putilla_escondida': awards_result.get('la_putilla_escondida'),
        'putilla_MVP': awards_result.get('putilla_MVP'),
        'putilla_basura': awards_result.get('putilla_basura')
    }
    
    # ========================================================================
    # STEP 6: RETURN STRUCTURED RESULTS
    # ========================================================================
    
    return {
        'team_awards': team_awards,      # 5 team-based awards
        'player_awards': player_awards,  # 3 player-based awards
        'total_stints': len(stint_values),  # How many putilla stints analyzed
        'current_week': current_week     # Current NBA season week
    }

# ============================================================================
# TEST/DEBUG SCRIPT
# ============================================================================

if __name__ == '__main__':
    """
    Test script to verify awards calculation works.
    
    Run this directly to test: python awards_api.py
    
    This will:
    1. Authenticate with Yahoo API
    2. Fetch roster data
    3. Calculate all awards
    4. Print results as formatted JSON
    
    Useful for debugging without running the full web server.
    """
    print("Testing awards wrapper...")
    
    # Authenticate and fetch data
    oauth = ensure_oauth(os.environ.get('YAHOO_OAUTH_FILE', 'oauth2.json'))
    rosters = get_all_teams_rosters(oauth)
    
    # Calculate awards
    awards = calculate_awards(rosters)
    
    # Pretty print results as JSON
    import json
    print(json.dumps(awards, indent=2))
