#!/usr/bin/env python3
"""
🏆 THE PUTILLA AWARDS v2 🏆
Most Valuable Waiver Wire Pickups

===============================================================================
PURPOSE
===============================================================================
This script calculates the "Putilla Awards" - awards for the most valuable
waiver wire pickups in a Yahoo Fantasy Basketball league. "Putillas" are 
players who have been dropped multiple times throughout the season (TRUE 
PUTILLAS = dropped 2+ times), making them waiver wire journeymen.

The key innovation in v2 is that it calculates value based on WHEN each player
was actually rostered, not just their full-season stats. This gives credit to
managers who streamed players during hot stretches.

===============================================================================
HOW IT WORKS
===============================================================================
1. Fetch all add/drop transactions from Yahoo Fantasy API
2. Build a timeline of when each player was on each team's roster
3. Count how many times each player was dropped (to identify TRUE PUTILLAS)
4. Prorate each player's season stats based on weeks rostered
5. Calculate fantasy value using 9-category scoring system
6. Award the highest-value pickups and rank managers by waiver wire skill

===============================================================================
AWARDS GIVEN
===============================================================================
- MVP Waiver Wire Pickup: Single most valuable roster stint
- Full Rankings: All qualified stints ranked by value
- Manager Rankings: Which managers extracted the most value from waivers
- Multi-Manager Players: Players who provided value on multiple teams
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Yahoo Fantasy API libraries
from yahoo_fantasy_api import game, league
from yahoo_oauth import OAuth2


# ============================================================================
# LEAGUE CONFIGURATION
# ============================================================================
# Standard 9-category head-to-head league settings
# These are the fantasy categories that count for scoring
CATEGORIES = ['FG%', 'FT%', 'PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO']

# Categories where LOWER is better (turnovers hurt you)
REVERSE_CATS = ['TO']


# ============================================================================
# YAHOO API AUTHENTICATION FUNCTIONS
# ============================================================================

def ensure_oauth(oauth_path: str) -> OAuth2:
    """
    Load Yahoo OAuth2 credentials from file.
    
    Yahoo Fantasy API requires OAuth2 authentication. This function:
    1. Checks if the oauth2.json file exists
    2. Loads the credentials from the file
    3. Refreshes the access token if it's expired
    
    Args:
        oauth_path: Path to oauth2.json file (contains access/refresh tokens)
    
    Returns:
        OAuth2 object with valid credentials
    
    Raises:
        SystemExit if oauth file not found
    """
    # Check if credentials file exists
    if not os.path.exists(oauth_path):
        print(f"oauth2 file not found at {oauth_path}", file=sys.stderr)
        sys.exit(1)

    # Load OAuth credentials from file
    # from_file=oauth_path tells OAuth2 to load existing credentials
    oauth = OAuth2(None, None, from_file=oauth_path)
    
    # Access tokens expire after a few hours, so refresh if needed
    if not oauth.token_is_valid():
        oauth.refresh_access_token()
    
    return oauth


def find_league_key(oauth: OAuth2, league_id: str) -> str:
    """
    Find the full Yahoo league key from a simple league ID.
    
    Yahoo uses full keys like "428.l.10145" where:
    - 428 is the game ID (changes each NBA season)
    - l means "league"
    - 10145 is the league ID
    
    This function converts "10145" -> "428.l.10145" for the current season.
    
    Args:
        oauth: Authenticated OAuth2 object
        league_id: Simple league ID like "10145"
    
    Returns:
        Full league key like "428.l.10145"
    
    Raises:
        RuntimeError if league not found
    """
    # Create Game object for NBA
    gm = game.Game(oauth, "nba")
    
    # Get all league keys for this user
    league_keys = gm.league_ids()

    # Find the key that matches our league ID
    for key in league_keys:
        if key.endswith(f".l.{league_id}"):
            return key

    # If we didn't find it, raise an error
    raise RuntimeError(f"League id {league_id} not found")


# ============================================================================
# YAHOO API HELPER FUNCTIONS
# ============================================================================

def api_get(oauth: OAuth2, url: str) -> Dict[str, Any]:
    """
    Make a GET request to Yahoo Fantasy API.
    
    Yahoo's API returns JSON data. This is a simple wrapper that:
    1. Makes the HTTP GET request using the OAuth session
    2. Checks for errors (raises exception if request failed)
    3. Parses and returns the JSON response
    
    Args:
        oauth: Authenticated OAuth2 object (has .session for requests)
        url: Full Yahoo Fantasy API URL to fetch
    
    Returns:
        Parsed JSON response as a Python dictionary
    
    Raises:
        HTTPError if request fails
    """
    # Use OAuth session to make authenticated request
    resp = oauth.session.get(url)
    
    # Check if request was successful (raises exception if not)
    resp.raise_for_status()
    
    # Parse JSON and return
    return resp.json()


def walk_get(obj: Any, key: str) -> Optional[Any]:
    """
    Find the first occurrence of a key in a nested JSON structure.
    
    Yahoo's API returns deeply nested JSON that's hard to navigate.
    Example structure:
    {
      "fantasy_content": {
        "league": [
          {
            "league_id": "10145",
            "current_week": 12
          },
          {
            "teams": {...}
          }
        ]
      }
    }
    
    This function recursively searches through all dicts and lists
    to find the first value for a given key name.
    
    Args:
        obj: The JSON object to search (dict, list, or primitive)
        key: The key name to search for (e.g. "current_week")
    
    Returns:
        The first value found for that key, or None if not found
    
    Example:
        walk_get(response, "current_week") -> 12
    """
    # If this is a dictionary, check if the key exists at this level
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        # Otherwise, recursively search all values in the dict
        for v in obj.values():
            found = walk_get(v, key)
            if found is not None:
                return found
    
    # If this is a list, recursively search each item
    elif isinstance(obj, list):
        for item in obj:
            found = walk_get(item, key)
            if found is not None:
                return found
    
    # If we didn't find it anywhere, return None
    return None


# ============================================================================
# TRANSACTION FETCHING FUNCTIONS
# ============================================================================

def fetch_all_transactions(oauth: OAuth2, league_key: str) -> List[Dict[str, Any]]:
    """
    Fetch all add/drop transactions from Yahoo Fantasy API with pagination.
    
    Yahoo's API returns transactions in pages of 50. This function:
    1. Fetches the first page (transactions 0-49)
    2. Checks if there are more transactions
    3. Fetches the next page (transactions 50-99)
    4. Repeats until all transactions are fetched
    
    Each transaction contains:
    - timestamp: When the transaction occurred
    - players: List of players added or dropped
    - team info: Which manager made the transaction
    
    Args:
        oauth: Authenticated OAuth2 object
        league_key: Full league key like "428.l.10145"
    
    Returns:
        List of all transaction dictionaries (all adds and drops for the season)
    
    Example transaction structure:
        {
          "timestamp": 1730123456,
          "players": {
            "0": {
              "player": [
                {"name": {"full": "Deni Avdija"}},
                {"transaction_data": {
                  "type": "add",
                  "source_type": "waivers",
                  "destination_team_name": "Team Name"
                }}
              ]
            }
          }
        }
    """
    transactions = []  # Will hold all transactions
    start = 0          # Starting index for pagination
    page_size = 50     # Yahoo returns max 50 transactions per request

    # Keep fetching pages until we run out of transactions
    while True:
        # Build API URL with pagination parameters
        # types=add,drop means we only want add and drop transactions (not trades)
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/league/"
            f"{league_key}/transactions;types=add,drop;start={start};count={page_size}?format=json"
        )
        
        # Fetch this page of transactions
        data = api_get(oauth, url)
        
        # Navigate the nested JSON structure to find transactions
        fc = data.get("fantasy_content", {})
        league_data = fc.get("league")
        
        # If no league data, we're done
        if not league_data or len(league_data) < 2:
            break

        # Get the transactions section
        # Yahoo returns: [metadata_dict, transactions_dict]
        txs = league_data[1].get("transactions", {})
        count = int(txs.get("count", 0))  # How many transactions in this page

        # Extract each transaction from this page
        chunk = []
        for i in range(count):
            # Transactions are indexed as "0", "1", "2", etc.
            tx_wrapper = txs.get(str(i))
            if tx_wrapper:
                tx = tx_wrapper.get("transaction")
                if tx:
                    chunk.append(tx)
        
        # If we didn't get any transactions, we're done
        if not chunk:
            break
        
        # Add this page's transactions to our list
        transactions.extend(chunk)
        
        # If we got fewer than page_size transactions, this was the last page
        if len(chunk) < page_size:
            break
        
        # Move to the next page
        start += page_size

    return transactions


def get_current_week(oauth: OAuth2, league_key: str) -> int:
    """
    Get the current week number from Yahoo Fantasy API.
    
    Fantasy basketball seasons are divided into weeks (usually ~23 weeks).
    This function queries the API to find out what week we're currently in.
    
    Args:
        oauth: Authenticated OAuth2 object
        league_key: Full league key like "428.l.10145"
    
    Returns:
        Current week number (e.g. 12), defaults to 12 if not found
    """
    # Fetch league metadata
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}?format=json"
    data = api_get(oauth, url)
    
    # Search the nested response for "current_week"
    current_week = walk_get(data, "current_week")
    
    # Return the week number, or default to 12 if not found
    return int(current_week) if current_week else 12


# ============================================================================
# ROSTER TIMELINE BUILDING
# ============================================================================

def build_roster_timeline(transactions: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict]], Dict[str, int]]:
    """
    Build a timeline of when each player was on each team's roster.
    
    This is the core data processing function. It takes all the add/drop
    transactions and reconstructs the history of every player's time on
    every team's roster.
    
    WHAT IT DOES:
    - Processes each transaction to extract adds and drops
    - Tracks when each player was added to a team
    - Tracks when each player was dropped from a team
    - Pairs adds with drops to create "roster stints"
    - Counts how many times each player was dropped
    
    ROSTER STINT: A continuous period when a player was on one team's roster
    Example: Player added on 11/15, dropped on 12/20 = one stint
    
    If a player is added by Team A, dropped, then added by Team B:
    - That creates TWO separate stints (one per team)
    - We can then calculate value for each stint independently
    
    Args:
        transactions: List of all add/drop transactions from Yahoo API
    
    Returns:
        Tuple of:
        1. roster_stints: Dict mapping player_name -> list of stint dicts
           Each stint has: team, team_key, add_timestamp, drop_timestamp,
                          player_key, nba_team, position
        2. drop_counts: Dict mapping player_name -> number of times dropped
           (Used to identify TRUE PUTILLAS who were dropped 2+ times)
    
    Example output:
        roster_stints = {
          "Deni Avdija": [
            {
              "team_name": "Team A",
              "add_timestamp": 1730123456,
              "drop_timestamp": 1731123456,
              ...
            },
            {
              "team_name": "Team B",
              "add_timestamp": 1732123456,
              "drop_timestamp": None,  # Still on roster
              ...
            }
          ]
        }
        
        drop_counts = {
          "Deni Avdija": 2  # Dropped twice (once by Team A, once by someone before Team A)
        }
    """
    # Track all add and drop events for each player
    # defaultdict creates an empty list automatically for new players
    player_events = defaultdict(list)
    
    # Count how many times each player was dropped (for TRUE PUTILLA filter)
    drop_counts = defaultdict(int)
    
    # Process each transaction to extract player events
    for tx in transactions:
        # Get transaction metadata (timestamp, etc.)
        tx_meta = tx[0] if isinstance(tx, list) else tx
        timestamp = int(tx_meta.get("timestamp", 0))
        
        # Get the players involved in this transaction
        players_section = walk_get(tx, "players")
        if not players_section:
            continue  # No players in this transaction (shouldn't happen)
            
        # How many players were involved (Yahoo can batch adds/drops)
        count = int(players_section.get("count", 0))
        
        # Process each player in this transaction
        for i in range(count):
            player_entry = players_section.get(str(i), {}).get("player")
            if not player_entry:
                continue
            
            # Extract player information from nested structure
            player_info = {}
            if isinstance(player_entry, list) and len(player_entry) > 0:
                # First element contains player metadata
                for item in player_entry[0]:
                    if isinstance(item, dict):
                        # Player name
                        if "name" in item:
                            player_info["name"] = item["name"].get("full", "Unknown")
                        # Unique player key (e.g. "428.p.12345")
                        if "player_key" in item:
                            player_info["player_key"] = item["player_key"]
                        # NBA team abbreviation (e.g. "WAS")
                        if "editorial_team_abbr" in item:
                            player_info["nba_team"] = item["editorial_team_abbr"]
                        # Position (e.g. "SF")
                        if "display_position" in item:
                            player_info["position"] = item["display_position"]
                
                # Second element contains transaction data (add or drop)
                if len(player_entry) > 1:
                    tx_data = player_entry[1].get("transaction_data", {})
                    if isinstance(tx_data, list):
                        tx_data = tx_data[0] if tx_data else {}
                    
                    # Extract transaction details
                    action = tx_data.get("type", "")              # "add" or "drop"
                    source = tx_data.get("source_type", "")       # "waivers" or "freeagents"
                    dest_team_key = tx_data.get("destination_team_key", "")
                    dest_team_name = tx_data.get("destination_team_name", "")
                    source_team_key = tx_data.get("source_team_key", "")
                    source_team_name = tx_data.get("source_team_name", "")
                    
                    player_name = player_info.get("name", "Unknown")
                    
                    # Record ADD events (from waivers or free agency)
                    if action == "add" and source in ["waivers", "freeagents"]:
                        player_events[player_name].append({
                            "type": "add",
                            "timestamp": timestamp,
                            "team_key": dest_team_key,
                            "team_name": dest_team_name,
                            "player_key": player_info.get("player_key"),
                            "nba_team": player_info.get("nba_team", ""),
                            "position": player_info.get("position", ""),
                            "source": source
                        })
                    
                    # Record DROP events
                    elif action == "drop":
                        # Count this drop for TRUE PUTILLA filtering
                        drop_counts[player_name] += 1
                        
                        player_events[player_name].append({
                            "type": "drop",
                            "timestamp": timestamp,
                            "team_key": source_team_key,
                            "team_name": source_team_name,
                            "player_key": player_info.get("player_key"),
                        })
    
    # Now convert events into roster stints by pairing adds with drops
    roster_stints = defaultdict(list)
    
    for player_name, events in player_events.items():
        # Sort events chronologically
        events.sort(key=lambda x: x["timestamp"])
        
        # Track active stints per team (one player can be on multiple teams)
        # team_key -> stint_info
        active_stints = {}
        
        for event in events:
            if event["type"] == "add":
                # Player was added to a team - start a new stint
                team_key = event["team_key"]
                active_stints[team_key] = {
                    "player_name": player_name,
                    "player_key": event.get("player_key"),
                    "team_key": team_key,
                    "team_name": event["team_name"],
                    "nba_team": event.get("nba_team", ""),
                    "position": event.get("position", ""),
                    "add_timestamp": event["timestamp"],
                    "drop_timestamp": None,  # Still on roster (for now)
                    "source": event.get("source", "")
                }
            
            elif event["type"] == "drop":
                # Player was dropped from a team - end the stint
                team_key = event["team_key"]
                if team_key in active_stints:
                    # Set the drop timestamp to complete the stint
                    active_stints[team_key]["drop_timestamp"] = event["timestamp"]
                    
                    # Save this completed stint
                    roster_stints[player_name].append(active_stints[team_key])
                    
                    # Remove from active stints
                    del active_stints[team_key]
        
        # Handle players still on rosters (no drop timestamp yet)
        for team_key, stint in active_stints.items():
            roster_stints[player_name].append(stint)
    
    return roster_stints, drop_counts


# ============================================================================
# PLAYER STATS FETCHING
# ============================================================================

def get_player_season_stats(oauth: OAuth2, league_key: str, player_key: str) -> Dict[str, float]:
    """
    Get a player's full season statistics from Yahoo Fantasy API.
    
    This fetches the cumulative season stats for a player across all games
    they've played. We'll prorate these later based on roster time.
    
    Stats fetched:
    - FG%: Field goal percentage
    - FT%: Free throw percentage  
    - 3PTM: Three-pointers made
    - PTS: Points
    - REB: Rebounds
    - AST: Assists
    - ST: Steals
    - BLK: Blocks
    - TO: Turnovers
    
    Args:
        oauth: Authenticated OAuth2 object
        league_key: Full league key like "428.l.10145"
        player_key: Unique player key like "428.p.5824" (Deni Avdija)
    
    Returns:
        Dictionary mapping category names to stat values
        Example: {"PTS": 456.0, "REB": 123.0, "AST": 89.0, ...}
        Returns empty dict if player not found or error occurs
    """
    # Build API URL to fetch player stats
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;player_keys={player_key}/stats?format=json"
    
    try:
        data = api_get(oauth, url)
        stats = {}
        
        # Navigate through Yahoo's nested JSON structure
        fc = data.get("fantasy_content", {})
        league_data = fc.get("league", [])
        
        if len(league_data) > 1:
            players = league_data[1].get("players", {})
            player = players.get("0", {}).get("player", [])
            
            if len(player) > 1:
                player_stats = player[1].get("player_stats", {})
                stats_list = player_stats.get("stats", [])
                
                # Yahoo uses numeric IDs for stat categories
                # Map Yahoo's IDs to our category names
                stat_map = {
                    "5": "FG%",    # Field goal percentage
                    "8": "FT%",    # Free throw percentage
                    "10": "3PTM",  # Three-pointers made
                    "12": "PTS",   # Points
                    "15": "REB",   # Rebounds
                    "16": "AST",   # Assists
                    "17": "ST",    # Steals
                    "18": "BLK",   # Blocks
                    "19": "TO"     # Turnovers
                }
                
                # Extract each stat value
                for stat in stats_list:
                    stat_data = stat.get("stat", {})
                    stat_id = str(stat_data.get("stat_id", ""))
                    value = stat_data.get("value", "0")
                    
                    # If this is a category we care about, save it
                    if stat_id in stat_map:
                        try:
                            # Convert to float, handle missing/invalid values
                            stats[stat_map[stat_id]] = float(value) if value not in ["-", ""] else 0.0
                        except:
                            stats[stat_map[stat_id]] = 0.0
        
        return stats
    
    except Exception as e:
        # If something goes wrong, return empty dict (player will be skipped)
        return {}


# ============================================================================
# VALUE CALCULATION FUNCTIONS
# ============================================================================

def calculate_stint_stats(oauth: OAuth2, league_key: str, stint: Dict, current_week: int, 
                          player_season_stats: Dict[str, float], season_weeks: int = 11) -> Dict[str, float]:
    """
    Calculate prorated statistics for a single roster stint.
    
    THE CORE INNOVATION: Instead of using full-season stats, we prorate the
    stats based on how long the player was actually on this manager's roster.
    
    EXAMPLE:
    - Player has 400 PTS for the full season (11 weeks)
    - Manager had player for 3 weeks (week 5-7)
    - Prorated PTS = 400 * (3/11) = 109 points
    
    This way, managers get credit proportional to how long they actually
    had the player rostered, not their full season performance.
    
    WHY PRORATE?
    We can't easily get week-by-week stats from Yahoo API, so we estimate
    by assuming stats accumulate evenly over the season. This isn't perfect
    (players get hot/cold), but it's a reasonable approximation.
    
    Args:
        oauth: Authenticated OAuth2 object (not used currently, kept for API)
        league_key: Full league key (not used currently, kept for API)
        stint: Dict with add_timestamp, drop_timestamp, team info
        current_week: What week number we're currently in (e.g. 12)
        player_season_stats: Full season stats from get_player_season_stats()
        season_weeks: How many weeks to use as denominator (defaults to 11)
    
    Returns:
        Dict with prorated stats plus metadata:
        - All 9 category values (prorated)
        - 'weeks': Number of weeks player was rostered
        - 'prorate_factor': Multiplier used (weeks_rostered / season_weeks)
    
    Example:
        stint = {
          "add_timestamp": 1730000000,  # Week 3
          "drop_timestamp": 1732000000   # Week 6
        }
        player_season_stats = {"PTS": 400, "REB": 150, ...}
        season_weeks = 11
        
        Result: {
          "PTS": 109.0,  # 400 * (3/11)
          "REB": 41.0,   # 150 * (3/11)
          "weeks": 3,
          "prorate_factor": 0.273
        }
    """
    # Get add and drop timestamps
    add_timestamp = stint.get("add_timestamp", 0)
    drop_timestamp = stint.get("drop_timestamp")
    
    # Convert Unix timestamps to fantasy week numbers
    start_week = timestamp_to_week(add_timestamp)
    if drop_timestamp:
        # Player was dropped - calculate end week
        end_week = timestamp_to_week(drop_timestamp)
    else:
        # Player still on roster - count up to current week
        end_week = current_week
    
    # Calculate how many weeks the player was rostered
    # max(1, ...) ensures at least 1 week (even if added/dropped same day)
    weeks_rostered = max(1, end_week - start_week + 1)
    
    # Can't have more weeks than the current week (sanity check)
    weeks_rostered = min(weeks_rostered, current_week)
    
    # Calculate proration multiplier
    # Example: 3 weeks rostered / 11 season weeks = 0.273
    prorate_factor = weeks_rostered / season_weeks
    
    # Prorate each stat category
    prorated_stats = {}
    for cat, value in player_season_stats.items():
        if cat in ['FG%', 'FT%']:
            # PERCENTAGES: Don't prorate! A player's shooting percentage is the
            # same whether they're on your roster for 1 week or 11 weeks.
            prorated_stats[cat] = value
        else:
            # COUNTING STATS: Multiply by prorate factor
            # Points, rebounds, etc. accumulate over time
            prorated_stats[cat] = value * prorate_factor
    
    # Add metadata about the stint duration
    prorated_stats['weeks'] = weeks_rostered
    prorated_stats['prorate_factor'] = prorate_factor
    
    return prorated_stats


def calculate_player_value(stats: Dict[str, float]) -> Tuple[float, Dict[str, float]]:
    """
    Calculate a player's fantasy value using 9-category scoring system.
    
    This function converts raw statistics into a single "value" number that
    represents how much the player helped their fantasy team win.
    
    SCORING SYSTEM:
    Different stats have different impacts on winning fantasy matchups:
    - Rare stats (steals, blocks) are worth MORE per unit
    - Common stats (points) are worth LESS per unit
    - Turnovers HURT you (negative value)
    - Efficiency matters: Well-rounded players get a bonus
    
    POINT VALUES (per stat point):
    - PTS: 0.03  (common, so less valuable per point)
    - REB: 0.06  (twice as valuable as points)
    - AST: 0.08  (slightly more valuable than rebounds)
    - STL: 0.35  (very rare, very valuable!)
    - BLK: 0.35  (very rare, very valuable!)
    - 3PM: 0.12  (moderately valuable)
    - TO:  -0.10 (penalty! turnovers hurt you)
    
    MINIMUM THRESHOLD:
    Player must have scored at least 100 points to qualify. This filters
    out players who barely played (garbage time pickups with no real impact).
    
    WELL-ROUNDED BONUS:
    If a player is good in multiple categories, they get a multiplier bonus:
    - 5-6 strong categories: 1.15x value (elite contributor)
    - 4 strong categories: 1.08x value (solid contributor)
    
    Strong category thresholds (for a stint):
    - 200+ PTS, 75+ REB, 50+ AST, 15+ STL, 12+ BLK, 25+ 3PM
    
    Args:
        stats: Dict with prorated stats (PTS, REB, AST, etc.)
    
    Returns:
        Tuple of:
        1. total_value: Overall fantasy value score (float)
           Returns -999 if player doesn't qualify (under 100 PTS)
        2. category_values: Dict showing value contribution per category
           Example: {"PTS": 13.68, "REB": 7.20, "AST": 4.80, ...}
    
    Example:
        stats = {"PTS": 456, "REB": 120, "AST": 60, "ST": 24, "BLK": 18, 
                 "3PTM": 45, "TO": 48}
        
        Returns: (67.84, {"PTS": 13.68, "REB": 7.20, ...})
    """
    category_values = {}
    total_value = 0.0
    
    # Extract stat values (default to 0 if missing)
    pts = stats.get('PTS', 0)
    reb = stats.get('REB', 0)
    ast = stats.get('AST', 0)
    stl = stats.get('ST', 0)
    blk = stats.get('BLK', 0)
    threes = stats.get('3PTM', 0)
    to = stats.get('TO', 0)
    
    # MINIMUM VOLUME CHECK
    # Must have at least 100 points to qualify
    # This filters out players with minimal playing time
    if pts < 100:
        return -999, {}  # Disqualified - not enough volume
    
    # Calculate value for each counting stat category
    pts_value = pts * 0.03
    category_values['PTS'] = pts_value
    
    reb_value = reb * 0.06
    category_values['REB'] = reb_value
    
    ast_value = ast * 0.08
    category_values['AST'] = ast_value
    
    stl_value = stl * 0.35  # Steals are very valuable!
    category_values['ST'] = stl_value
    
    blk_value = blk * 0.35  # Blocks are very valuable!
    category_values['BLK'] = blk_value
    
    three_value = threes * 0.12
    category_values['3PTM'] = three_value
    
    to_penalty = to * -0.10  # Turnovers HURT you
    category_values['TO'] = to_penalty
    
    # Sum up base value from all categories
    total_value = (pts_value + reb_value + ast_value + stl_value + 
                   blk_value + three_value + to_penalty)
    
    # WELL-ROUNDED CONTRIBUTOR BONUS
    # Count how many categories the player is "good" in
    good_cats = 0
    if pts >= 200: good_cats += 1   # Strong scorer
    if reb >= 75: good_cats += 1    # Strong rebounder
    if ast >= 50: good_cats += 1    # Good passer
    if stl >= 15: good_cats += 1    # Good thief
    if blk >= 12: good_cats += 1    # Good shot blocker
    if threes >= 25: good_cats += 1 # Good three-point shooter
    
    # Apply bonus multiplier if player is well-rounded
    if good_cats >= 5:
        # Elite contributor - strong in 5-6 categories
        total_value *= 1.15
    elif good_cats >= 4:
        # Solid contributor - strong in 4 categories
        total_value *= 1.08
    
    return total_value, category_values


def timestamp_to_week(timestamp: int, season_start: datetime = None) -> int:
    """
    Convert a Unix timestamp to a fantasy week number.
    
    Fantasy weeks are numbered 1, 2, 3, ... based on days since season start.
    Each week is 7 days.
    
    FORMULA:
    1. Calculate days since season started
    2. Divide by 7 to get weeks (round down)
    3. Add 1 (week numbers start at 1, not 0)
    
    Args:
        timestamp: Unix timestamp (seconds since Jan 1, 1970)
        season_start: When the fantasy season started (defaults to Oct 20, 2025)
    
    Returns:
        Week number (1-23), capped at 23 maximum
    
    Example:
        season_start = Oct 20, 2025
        timestamp for Nov 10, 2025 (21 days later)
        Result: (21 // 7) + 1 = week 4
    """
    if season_start is None:
        # NBA 2025-26 season started around Oct 21, 2025
        season_start = datetime(2025, 10, 20)
    
    # Convert timestamp to datetime object
    event_date = datetime.fromtimestamp(timestamp)
    
    # Calculate days since season start
    days_since_start = (event_date - season_start).days
    
    # Convert to week number (minimum week 1)
    week = max(1, (days_since_start // 7) + 1)
    
    # Cap at 23 weeks (typical NBA fantasy season length)
    return min(week, 23)


# ============================================================================
# STINT ANALYSIS ORCHESTRATION
# ============================================================================

def analyze_roster_stints(oauth: OAuth2, league_key: str, roster_stints: Dict[str, List[Dict]], current_week: int, drop_counts: Dict[str, int] = None) -> List[Dict]:
    """
    Analyze the value of every roster stint for all TRUE PUTILLAS.
    
    This is the main analysis orchestrator that:
    1. Fetches season stats for all unique players (with caching)
    2. For each roster stint, calculates prorated stats
    3. Converts prorated stats into a fantasy value score
    4. Returns a sorted list of all stints by value
    
    IMPORTANT: Each stint is treated independently. If Deni Avdija was
    picked up by 3 different managers, that creates 3 separate entries
    in the results, each with their own value calculation.
    
    WHY CACHE STATS?
    A player might have multiple stints (multiple teams), but their season
    stats are the same. We fetch stats once per player and reuse for all
    that player's stints to avoid redundant API calls.
    
    Args:
        oauth: Authenticated OAuth2 object for API calls
        league_key: Full league key like "428.l.10145"
        roster_stints: Dict from build_roster_timeline() with all stints
        current_week: Current fantasy week number
        drop_counts: Dict mapping player names to drop counts (for metadata)
    
    Returns:
        List of stint value dicts, sorted by total_value (highest first)
        Each dict contains:
        - player_name, team_name, add_date, drop_date
        - weeks: Number of weeks rostered
        - stats: Prorated statistics
        - total_value: Overall fantasy value score
        - category_values: Value breakdown by category
        - still_rostered: Boolean if player not yet dropped
        - drop_count: Times this player was dropped league-wide
    """
    print("\n📊 Analyzing roster stints...")
    print("   (Using prorated stats based on weeks rostered)")
    
    # Initialize drop counts if not provided
    if drop_counts is None:
        drop_counts = {}
    
    # -------------------------------------------------------------------------
    # STEP 1: Fetch season stats for all unique players (with progress updates)
    # -------------------------------------------------------------------------
    print("\n   Fetching player season stats...")
    
    # Cache to avoid fetching same player's stats multiple times
    player_stats_cache = {}
    
    # Build set of unique (player_name, player_key) tuples
    unique_players = set()
    for player_name, stints in roster_stints.items():
        for stint in stints:
            player_key = stint.get("player_key")
            if player_key:
                unique_players.add((player_name, player_key))
    
    total_players = len(unique_players)
    processed = 0
    
    # Fetch stats for each unique player
    for player_name, player_key in unique_players:
        stats = get_player_season_stats(oauth, league_key, player_key)
        if stats:
            player_stats_cache[player_key] = stats
        
        # Progress update every 20 players
        processed += 1
        if processed % 20 == 0:
            print(f"   Fetched stats for {processed}/{total_players} players...")
    
    print(f"   Got stats for {len(player_stats_cache)} players")
    
    # -------------------------------------------------------------------------
    # STEP 2: Calculate value for each individual roster stint
    # -------------------------------------------------------------------------
    print("\n   Calculating stint values...")
    stint_values = []
    
    for player_name, stints in roster_stints.items():
        for stint in stints:
            player_key = stint.get("player_key")
            team_name = stint.get("team_name", "Unknown")
            
            # Skip if we don't have stats for this player
            if player_key not in player_stats_cache:
                continue
            
            # Get the player's full season stats
            season_stats = player_stats_cache[player_key]
            
            # Prorate stats based on this specific stint duration
            stint_stats = calculate_stint_stats(
                oauth, league_key, stint, current_week, 
                season_stats, season_weeks=current_week
            )
            
            # Only process stints that have valid duration
            if stint_stats and stint_stats.get('weeks', 0) > 0:
                # Calculate fantasy value from prorated stats
                value, cat_values = calculate_player_value(stint_stats)
                
                # Only include if player qualified (100+ PTS minimum)
                if value > -999:
                    # Format dates for display
                    add_date = datetime.fromtimestamp(stint.get("add_timestamp", 0)).strftime("%m/%d")
                    drop_date = "now" if not stint.get("drop_timestamp") else datetime.fromtimestamp(stint["drop_timestamp"]).strftime("%m/%d")
                    
                    # Build complete stint result
                    stint_values.append({
                        "player_name": player_name,
                        "player_key": player_key,
                        "team_name": team_name,
                        "team_key": stint.get("team_key"),
                        "nba_team": stint.get("nba_team", ""),
                        "position": stint.get("position", ""),
                        "add_date": add_date,
                        "drop_date": drop_date,
                        "weeks": int(stint_stats.get("weeks", 0)),
                        "stats": stint_stats,
                        "total_value": value,
                        "category_values": cat_values,
                        "still_rostered": stint.get("drop_timestamp") is None,
                        "drop_count": drop_counts.get(player_name, 0)
                    })
    
    # Sort all stints by value (highest first)
    stint_values.sort(key=lambda x: x["total_value"], reverse=True)
    
    return stint_values


# ============================================================================
# RESULTS DISPLAY FUNCTIONS
# ============================================================================

def print_putilla_awards(stint_values: List[Dict]):
    """
    Print the Putilla Awards results in a beautifully formatted report.
    
    This function generates a comprehensive report showing:
    1. MVP Waiver Wire Pickup (single best stint)
    2. Full Rankings (all qualified stints)
    3. Multi-Manager Players (players valuable on multiple teams)
    4. Manager Rankings (who extracted most value from waivers)
    5. Each Manager's Best Pickup
    6. Detailed Breakdown for Winner
    
    The report is formatted with:
    - Emoji decorations for visual appeal
    - Aligned columns for easy reading
    - Medals for top 3 (🥇🥈🥉)
    - Detailed stats breakdowns
    
    Args:
        stint_values: List of stint dicts from analyze_roster_stints(),
                     sorted by total_value (highest first)
    """
    
    # Filter to only include stints with positive value
    qualified = [s for s in stint_values if s['total_value'] > 0]
    
    # Print header banner
    print("\n" + "="*75)
    print("🏆" + " "*27 + "THE PUTILLA AWARDS" + " "*27 + "🏆")
    print("="*75)
    print("     Most Valuable Waiver Wire Pickups (Based on Actual Roster Time)")
    print("="*75)
    
    # Handle case with no qualified pickups
    if not qualified:
        print("\nNo waiver wire pickups found with sufficient data.")
        return
    
    # -------------------------------------------------------------------------
    # SECTION 1: MVP Award (Single Best Stint)
    # -------------------------------------------------------------------------
    print("\n" + "🥇 PUTILLA AWARD WINNER - MVP WAIVER WIRE PICKUP 🥇")
    print("-"*75)
    winner = qualified[0]  # Highest value stint
    
    # Print winner details
    print(f"\n   {winner['player_name']} ({winner['position']} - {winner['nba_team']})")
    print(f"   Times Dropped League-Wide: {winner.get('drop_count', 0)}")
    print(f"   Picked up by: {winner['team_name']}")
    print(f"   Roster Period: {winner['add_date']} → {winner['drop_date']}")
    print(f"   Weeks Rostered: {winner['weeks']}")
    print(f"   Value Score: {winner['total_value']:.2f}")
    
    # Print winner's prorated stats
    print(f"\n   Stats While Rostered (prorated):")
    for cat in ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO']:
        if cat in winner['stats']:
            print(f"      {cat}: {winner['stats'][cat]:.0f}")
    
    # -------------------------------------------------------------------------
    # SECTION 2: Full Rankings (All Qualified Stints)
    # -------------------------------------------------------------------------
    print("\n\n" + f"📋 ALL QUALIFIED WAIVER WIRE PICKUPS ({len(qualified)} total stints)" )
    print("-"*90)
    print(f"{'Rank':<4} {'Player':<20} {'Drops':<5} {'Value':>7} {'Period':<13} {'Manager':<22}")
    print("-"*90)
    
    # Print each stint in ranked order
    for i, stint in enumerate(qualified, 1):
        # Add medal emoji for top 3
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        
        # Format roster period
        period = f"{stint['add_date']}-{stint['drop_date']}"
        drop_count = stint.get('drop_count', 0)
        
        # Print row with aligned columns
        print(f"{medal}{i:<2} {stint['player_name']:<20} {drop_count:<5} "
              f"{stint['total_value']:>7.2f} {period:<13} {stint['team_name']:<22}")
    
    # -------------------------------------------------------------------------
    # SECTION 3: Players Who Provided Value on Multiple Teams
    # -------------------------------------------------------------------------
    print("\n\n" + "🔄 PLAYERS WITH VALUE SPLIT ACROSS MULTIPLE MANAGERS")
    print("-"*75)
    
    # Group stints by player name
    player_stints = defaultdict(list)
    for stint in qualified:
        player_stints[stint['player_name']].append(stint)
    
    # Find players rostered by multiple teams
    multi_manager_players = [
        (name, stints) for name, stints in player_stints.items() 
        if len(set(s['team_name'] for s in stints)) > 1  # More than one unique team
    ]
    
    # Sort by total value across all teams
    multi_manager_players.sort(
        key=lambda x: sum(s['total_value'] for s in x[1]), 
        reverse=True
    )
    
    # Print multi-manager players
    if multi_manager_players:
        print(f"{'Player':<22} {'Total Value':>12} {'Stints'}")
        print("-"*75)
        
        # Show top 10 multi-manager players
        for player_name, stints in multi_manager_players[:10]:
            total_val = sum(s['total_value'] for s in stints)
            
            # Format stint details: "TeamName(value), TeamName(value)"
            stint_details = ", ".join([
                f"{s['team_name'][:15]}({s['total_value']:.1f})" 
                for s in sorted(stints, key=lambda x: x['total_value'], reverse=True)
            ])
            
            print(f"{player_name:<22} {total_val:>12.2f} {stint_details}")
    else:
        print("No players had qualifying stints on multiple teams.")
    
    # -------------------------------------------------------------------------
    # SECTION 4: Manager Rankings (Total Waiver Wire Value)
    # -------------------------------------------------------------------------
    print("\n\n" + "👑 WAIVER WIRE MANAGER RANKINGS" + " "*30)
    print("-"*75)
    
    # Aggregate value by team
    team_values = defaultdict(lambda: {"total": 0, "stints": [], "count": 0})
    for stint in qualified:
        team = stint['team_name']
        if team:
            team_values[team]["total"] += stint['total_value']
            team_values[team]["stints"].append((
                stint['player_name'], 
                stint['total_value'], 
                stint['weeks']
            ))
            team_values[team]["count"] += 1
    
    # Sort teams by total value
    sorted_teams = sorted(
        team_values.items(), 
        key=lambda x: x[1]["total"], 
        reverse=True
    )
    
    # Print team rankings
    print(f"\n{'Rank':<4} {'Manager':<30} {'Value':>10} {'Stints':>8}")
    print("-"*55)
    
    for i, (team, data) in enumerate(sorted_teams, 1):
        # Add medal emoji for top 3
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        print(f"{medal}{i:<2} {team:<30} {data['total']:>10.2f} {data['count']:>8}")
    
    # -------------------------------------------------------------------------
    # SECTION 5: Each Manager's Best Pickup
    # -------------------------------------------------------------------------
    print("\n\n" + "🌟 EACH MANAGER'S BEST WAIVER PICKUP" + " "*25)
    print("-"*75)
    print(f"{'Manager':<30} {'Best Pickup':<20} {'Value':>8} {'Weeks':>6}")
    print("-"*75)
    
    for team, data in sorted_teams:
        if data['stints']:
            # Find the highest value stint for this team
            best = max(data['stints'], key=lambda x: x[1])
            print(f"{team:<30} {best[0]:<20} {best[1]:>8.2f} {best[2]:>6}")
    
    # -------------------------------------------------------------------------
    # SECTION 6: Detailed Breakdown for Winner
    # -------------------------------------------------------------------------
    if sorted_teams:
        print("\n\n" + f"📊 DETAILED BREAKDOWN: {sorted_teams[0][0]}")
        print("-"*75)
        winner_data = sorted_teams[0][1]
        
        print(f"\nTotal Value Added: {winner_data['total']:.2f}")
        print(f"Qualified Roster Stints: {winner_data['count']}")
        print(f"\nAll Qualified Stints (sorted by value):")
        print(f"{'Rank':<4} {'Player':<22} {'Value':>10} {'Weeks':>6}")
        print("-"*45)
        
        # Show all stints for the winning manager
        sorted_stints = sorted(winner_data['stints'], key=lambda x: x[1], reverse=True)
        for i, (player, value, weeks) in enumerate(sorted_stints, 1):
            print(f"{i:<4} {player:<22} {value:>10.2f} {weeks:>6}")
    
    # Print footer banner
    print("\n" + "="*75)
    print("🏆" + " "*22 + "END OF PUTILLA AWARDS" + " "*22 + "🏆")
    print("="*75)


# ============================================================================
# MAIN SCRIPT EXECUTION
# ============================================================================

def main():
    """
    Main execution function that orchestrates the entire Putilla Awards process.
    
    EXECUTION FLOW:
    1. Parse command line arguments (league ID, OAuth file path)
    2. Authenticate with Yahoo OAuth2
    3. Find the full league key
    4. Get current week number
    5. Fetch all add/drop transactions for the season
    6. Build roster timeline (track when each player was on each team)
    7. Filter to TRUE PUTILLAS (players dropped 2+ times)
    8. Analyze each stint to calculate fantasy value
    9. Print comprehensive awards report
    
    COMMAND LINE USAGE:
        python putilla_awards_v2.py --league-id 10145 --oauth-file oauth2.json
    
    DEFAULTS:
        --league-id defaults to "10145" (Chantasy 2025-26 league)
        --oauth-file defaults to "oauth2.json" in current directory
    
    TRUE PUTILLAS FILTER:
    We only analyze players who have been dropped 2+ times (min_drops = 2).
    This filters out:
    - Drafted players who stayed rostered all season
    - One-time pickups that worked out
    
    We focus on the waiver wire journeymen - the players who bounced around
    but still provided value to the managers who timed their pickups well.
    """
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="🏆 The Putilla Awards v2 - Based on Actual Roster Time"
    )
    parser.add_argument(
        "--league-id", 
        default="10145", 
        help="Yahoo league ID (just the number, e.g. 10145)"
    )
    parser.add_argument(
        "--oauth-file", 
        default="oauth2.json", 
        help="Path to OAuth2 credentials file"
    )
    args = parser.parse_args()

    # Print startup banner
    print("\n" + "="*75)
    print("🏆 THE PUTILLA AWARDS v2 - Roster-Time Based Analysis 🏆")
    print("="*75)

    # -------------------------------------------------------------------------
    # STEP 1: Authentication & League Setup
    # -------------------------------------------------------------------------
    # Load OAuth credentials and authenticate with Yahoo
    oauth = ensure_oauth(args.oauth_file)
    
    # Convert simple league ID (10145) to full league key (428.l.10145)
    league_key = find_league_key(oauth, args.league_id)
    print(f"\n🏀 League: {league_key}")
    
    # Get current week number (determines prorating calculations)
    current_week = get_current_week(oauth, league_key)
    print(f"📅 Current Week: {current_week}")
    
    # -------------------------------------------------------------------------
    # STEP 2: Fetch Transaction Data
    # -------------------------------------------------------------------------
    # Fetch all add/drop transactions from Yahoo API (with pagination)
    print("\n📥 Fetching transactions...")
    transactions = fetch_all_transactions(oauth, league_key)
    print(f"   Found {len(transactions)} transactions")
    
    # -------------------------------------------------------------------------
    # STEP 3: Build Roster Timeline
    # -------------------------------------------------------------------------
    # Process transactions into roster stints
    # Each stint represents one continuous period a player was on one team
    print("\n🔍 Building roster timeline...")
    roster_stints, drop_counts = build_roster_timeline(transactions)
    
    # Calculate statistics
    total_stints = sum(len(stints) for stints in roster_stints.values())
    print(f"   Found {len(roster_stints)} unique players with {total_stints} total roster stints")
    
    # -------------------------------------------------------------------------
    # STEP 4: Filter to TRUE PUTILLAS
    # -------------------------------------------------------------------------
    # Only keep players who were dropped 2+ times
    # This is the key filter that defines "PUTILLAS"
    min_drops = 2
    putilla_stints = {
        player: stints for player, stints in roster_stints.items() 
        if drop_counts.get(player, 0) >= min_drops
    }
    
    putilla_total = sum(len(stints) for stints in putilla_stints.values())
    print(f"   Filtered to {len(putilla_stints)} TRUE PUTILLAS (dropped {min_drops}+ times) with {putilla_total} stints")
    
    # -------------------------------------------------------------------------
    # STEP 5: Analyze Value of Each Stint
    # -------------------------------------------------------------------------
    # For each stint:
    # 1. Fetch player's season stats
    # 2. Prorate stats based on weeks rostered
    # 3. Calculate fantasy value from prorated stats
    stint_values = analyze_roster_stints(
        oauth, 
        league_key, 
        putilla_stints,  # Only TRUE PUTILLAS
        current_week, 
        drop_counts
    )
    
    # -------------------------------------------------------------------------
    # STEP 6: Print Awards Report
    # -------------------------------------------------------------------------
    # Generate comprehensive formatted report with all awards
    print_putilla_awards(stint_values)


# ============================================================================
# SCRIPT ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    """
    Entry point when script is run directly (not imported as a module).
    
    This allows the script to be run from command line:
        python putilla_awards_v2.py --league-id 10145
    
    Or imported in other scripts without auto-executing:
        from putilla_awards_v2 import analyze_roster_stints
    """
    main()
