#!/usr/bin/env python3
"""
Waiver optimizer for Yahoo Fantasy Basketball matchups.

The optimizer evaluates add/drop scenarios for the current matchup and ranks
them by estimated matchup win-probability gain.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from yahoo_fantasy_api import game, League
from yahoo_oauth import OAuth2


DEFAULT_LEAGUE_KEY = os.environ.get("FANTASY_LEAGUE_KEY", "466.l.10145")
DEFAULT_TEAM_KEY = os.environ.get("FANTASY_TEAM_KEY", "466.l.10145.t.12")
DEFAULT_OAUTH_FILE = os.environ.get("YAHOO_OAUTH_FILE", "oauth2.json")

ALL_CATEGORIES = ["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"]
COUNTING_CATEGORIES = ["3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"]
REVERSE_CATEGORIES = {"TO"}
PROJECTION_WINDOWS = {"recent_2w", "season"}

STAT_MAP = {
    "5": "FG%",
    "8": "FT%",
    "10": "3PTM",
    "12": "PTS",
    "15": "REB",
    "16": "AST",
    "17": "ST",
    "18": "BLK",
    "19": "TO",
}

STATUS_MULTIPLIER = {
    "": 1.0,
    "DTD": 0.85,
    "GTD": 0.75,
    "NA": 0.0,
    "O": 0.0,
    "INJ": 0.0,
    "IR": 0.0,
    "IL": 0.0,
    "IL+": 0.0,
}

NBA_TEAM_CODES = {
    "ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
    "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
    "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WSH",
}

TEAM_ABBR_ALIASES = {
    "GS": "GSW",
    "GSW": "GSW",
    "PHO": "PHX",
    "PHX": "PHX",
    "NO": "NOP",
    "NOP": "NOP",
    "NY": "NYK",
    "NYK": "NYK",
    "SA": "SAS",
    "SAS": "SAS",
    "BRK": "BKN",
    "BKN": "BKN",
    "CHO": "CHA",
    "CHA": "CHA",
    "WAS": "WSH",
    "WSH": "WSH",
    "UTA": "UTA",
    "UTAH": "UTA",
}


class WaiverAnalysisError(RuntimeError):
    """Domain error for waiver optimization."""


def safe_float(val: Any) -> float:
    """Convert Yahoo stat values to float, handling blanks."""
    if val in (None, "", "-"):
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def to_percent(value: float, digits: int = 1) -> float:
    return round(value * 100.0, digits)


def normalize_category(cat: str) -> str:
    """Normalize user-facing category names to internal keys."""
    cleaned = (cat or "").strip().upper().replace(" ", "")
    alias = {
        "3PM": "3PTM",
        "3PT": "3PTM",
        "3PTM": "3PTM",
        "STL": "ST",
        "ST": "ST",
        "FG": "FG%",
        "FG%": "FG%",
        "FT": "FT%",
        "FT%": "FT%",
        "PTS": "PTS",
        "REB": "REB",
        "AST": "AST",
        "BLK": "BLK",
        "TO": "TO",
        "TOV": "TO",
    }
    return alias.get(cleaned, cleaned)


def normalize_name_token(name: str) -> str:
    """Normalize player name for resilient user-input matching."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())


def normalize_team(team_abbr: str) -> str:
    team = (team_abbr or "").strip().upper()
    return TEAM_ABBR_ALIASES.get(team, team)


def daterange(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def walk_values(obj: Any, key: str) -> Iterable[Any]:
    """Yield all values for a key inside nested dict/list structures."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from walk_values(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_values(item, key)


def extract_category_stats(payload: Dict[str, Any]) -> Dict[str, float]:
    """Extract mapped category values from any Yahoo stats payload."""
    stats: Dict[str, float] = {}

    for stat_obj in walk_values(payload, "stat"):
        if not isinstance(stat_obj, dict):
            continue
        if "value" not in stat_obj:
            continue
        stat_id = str(stat_obj.get("stat_id", ""))
        stat_value = safe_float(stat_obj.get("value"))
        if stat_id in STAT_MAP:
            stats[STAT_MAP[stat_id]] = stat_value
            continue

        # Fallback to display name if stat_id mapping changed in Yahoo response.
        display = normalize_category(str(stat_obj.get("display_name", "")))
        if display in ALL_CATEGORIES:
            stats[display] = stat_value

    return stats


def categories_to_totals(cat_values: Dict[str, float]) -> Dict[str, float]:
    """
    Convert category totals into extended totals with estimated shot volume.

    Yahoo current-week team stats usually provide the 9 categories, so we
    estimate FGA/FTA from points and percentages to combine with projections.
    """
    totals = {
        "3PTM": cat_values.get("3PTM", 0.0),
        "PTS": cat_values.get("PTS", 0.0),
        "REB": cat_values.get("REB", 0.0),
        "AST": cat_values.get("AST", 0.0),
        "ST": cat_values.get("ST", 0.0),
        "BLK": cat_values.get("BLK", 0.0),
        "TO": cat_values.get("TO", 0.0),
        "FGM": 0.0,
        "FGA": 0.0,
        "FTM": 0.0,
        "FTA": 0.0,
    }

    fg_pct = clamp(cat_values.get("FG%", 0.45), 0.0, 1.0)
    ft_pct = clamp(cat_values.get("FT%", 0.75), 0.0, 1.0)
    pts_total = max(totals["PTS"], 0.0)

    fga = (pts_total * 0.55) / max(fg_pct, 0.25) if pts_total > 0 else 0.0
    fta = (pts_total * 0.18) / max(ft_pct, 0.4) if pts_total > 0 else 0.0

    totals["FGM"] = fga * fg_pct
    totals["FGA"] = fga
    totals["FTM"] = fta * ft_pct
    totals["FTA"] = fta
    totals["FG%"] = fg_pct
    totals["FT%"] = ft_pct
    return totals


def combine_totals(current: Dict[str, float], remaining: Dict[str, float]) -> Dict[str, float]:
    """Combine current-week score with remaining-week projection."""
    combined = {}
    for cat in COUNTING_CATEGORIES + ["FGM", "FGA", "FTM", "FTA"]:
        combined[cat] = current.get(cat, 0.0) + remaining.get(cat, 0.0)
    combined["FG%"] = combined["FGM"] / combined["FGA"] if combined["FGA"] > 0 else 0.0
    combined["FT%"] = combined["FTM"] / combined["FTA"] if combined["FTA"] > 0 else 0.0
    return combined


def fetch_team_week_current_totals(oauth: OAuth2, team_key: str, week: int) -> Dict[str, float]:
    """Fetch current matchup score for one team in one week."""
    url = (
        f"https://fantasysports.yahooapis.com/fantasy/v2/team/"
        f"{team_key}/stats;type=week;week={week}?format=json"
    )
    payload = api_get(oauth, url)
    cat_values = extract_category_stats(payload)
    return categories_to_totals(cat_values)


def fetch_team_roster_current(team_obj: Any, week: int, today: date) -> List[Dict[str, Any]]:
    """
    Fetch the most up-to-date roster snapshot possible.

    Prefer day-level roster for `today` so recent adds/drops are reflected.
    Fall back to week-level if day-level is unavailable.
    """
    try:
        return team_obj.roster(day=today)
    except Exception:
        return team_obj.roster(week=week)


def fetch_percent_owned_map(lg: League, player_ids: List[int]) -> Dict[int, float]:
    """Fetch Yahoo percent-owned values for player ids."""
    out: Dict[int, float] = {}
    if not player_ids:
        return out

    deduped = sorted(set(int(pid) for pid in player_ids if pid))
    for i in range(0, len(deduped), 25):
        chunk = deduped[i:i + 25]
        try:
            rows = lg.percent_owned(chunk)
        except Exception:
            continue
        for row in rows:
            try:
                pid = int(row.get("player_id", 0))
            except (TypeError, ValueError):
                continue
            if pid:
                out[pid] = safe_float(row.get("percent_owned"))
    return out


def fetch_player_detail_signals(lg: League, player_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """
    Fetch lightweight player status/news signals from Yahoo player details.

    Yahoo coverage varies by player; this safely falls back when fields are absent.
    """
    out: Dict[int, Dict[str, Any]] = {}
    if not player_ids:
        return out

    try:
        details = lg.player_details(sorted(set(int(pid) for pid in player_ids if pid)))
    except Exception:
        return out

    if not isinstance(details, list):
        return out

    for detail in details:
        if not isinstance(detail, dict):
            continue
        try:
            pid = int(detail.get("player_id", 0))
        except (TypeError, ValueError):
            pid = 0
        if not pid:
            continue

        status = str(detail.get("status", "") or "").strip().upper()
        status_full = str(detail.get("status_full", "") or "").strip()

        news_fields = [
            "editorial_injury_status",
            "editorial_injury_description",
            "editorial_injury_note",
            "injury_note",
            "editorial_player_notes",
            "player_note",
        ]
        news_parts: List[str] = []
        for field in news_fields:
            value = detail.get(field)
            if value:
                text = str(value).strip()
                if text and text not in news_parts:
                    news_parts.append(text)

        return_hint = ""
        for field in ["injury_return_date", "expected_return", "editorial_expected_return"]:
            value = detail.get(field)
            if value:
                return_hint = str(value).strip()
                break

        if not news_parts:
            has_notes = str(detail.get("has_player_notes", "0")).lower() in {"1", "true", "yes"}
            if has_notes:
                news_parts.append("Yahoo player note available.")

        out[pid] = {
            "status": status,
            "status_full": status_full,
            "news": " | ".join(news_parts[:2]),
            "return_hint": return_hint,
            "is_undroppable": str(detail.get("is_undroppable", "0")).lower() in {"1", "true", "yes"},
        }

    return out


def ensure_oauth(oauth_path: str = DEFAULT_OAUTH_FILE) -> OAuth2:
    """Load OAuth credentials and refresh if needed."""
    if not os.path.exists(oauth_path):
        raise WaiverAnalysisError(f"oauth2 file not found at {oauth_path}")

    oauth = OAuth2(None, None, from_file=oauth_path)
    if not oauth.token_is_valid():
        oauth.refresh_access_token()
    return oauth


def api_get(oauth: OAuth2, url: str) -> Dict[str, Any]:
    """JSON GET through authenticated Yahoo session."""
    resp = oauth.session.get(url, timeout=25)
    resp.raise_for_status()
    return resp.json()


def resolve_league(gm: game.Game, preferred_league_key: str) -> League:
    """Resolve a usable league key for the authenticated account."""
    available = gm.league_ids()
    if preferred_league_key in available:
        return gm.to_league(preferred_league_key)

    if ".l." in preferred_league_key:
        league_id = preferred_league_key.split(".l.")[-1]
        for key in available:
            if key.endswith(f".l.{league_id}"):
                return gm.to_league(key)

    if available:
        return gm.to_league(available[0])

    raise WaiverAnalysisError("No NBA league found on this Yahoo account.")


def resolve_team_key(lg: League, preferred_team_key: str) -> str:
    """Resolve team key within the current league."""
    teams = lg.teams()
    if preferred_team_key in teams:
        return preferred_team_key

    if teams:
        # Typical account has one team in a league.
        return sorted(teams.keys())[0]

    raise WaiverAnalysisError("No teams found in this league.")


def parse_player_node(player_node: List[Any]) -> Dict[str, Any]:
    """Parse player metadata + mapped stats from Yahoo player node."""
    meta = {
        "player_key": "",
        "player_id": 0,
        "name": "",
        "position": "",
        "nba_team": "",
        "status": "",
        "stats": {},
    }

    meta_block = player_node[0] if len(player_node) > 0 else []
    for item in meta_block:
        if not isinstance(item, dict):
            continue
        if "player_key" in item:
            meta["player_key"] = item["player_key"]
        elif "player_id" in item:
            meta["player_id"] = int(safe_float(item["player_id"]))
        elif "name" in item:
            meta["name"] = item["name"].get("full", "")
        elif "display_position" in item:
            meta["position"] = item["display_position"]
        elif "editorial_team_abbr" in item:
            meta["nba_team"] = item["editorial_team_abbr"]
        elif "status" in item and isinstance(item["status"], str):
            meta["status"] = item["status"].strip().upper()

    stats_block = {}
    if len(player_node) > 1 and isinstance(player_node[1], dict):
        stats_block = player_node[1].get("player_stats", {})

    for row in stats_block.get("stats", []):
        stat = row.get("stat", {})
        stat_id = str(stat.get("stat_id", ""))
        if stat_id in STAT_MAP:
            meta["stats"][STAT_MAP[stat_id]] = safe_float(stat.get("value"))

    return meta


def parse_players_response(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract player rows from Yahoo players response payload."""
    league_section = data.get("fantasy_content", {}).get("league", [])
    if len(league_section) < 2:
        return []

    players_block = league_section[1].get("players", {})
    out: List[Dict[str, Any]] = []

    for key, wrapped in players_block.items():
        if key == "count":
            continue
        player_node = wrapped.get("player", [])
        if not player_node:
            continue
        parsed = parse_player_node(player_node)
        if parsed["player_key"]:
            out.append(parsed)

    return out


def yahoo_players_request(
    oauth: OAuth2, league_key: str, endpoint: str, stats_type: str
) -> Tuple[List[Dict[str, Any]], str]:
    """Call Yahoo players endpoint with preferred stats_type and fallback."""
    url_with_type = (
        f"https://fantasysports.yahooapis.com/fantasy/v2/league/"
        f"{league_key}/{endpoint}/stats;type={stats_type}?format=json"
    )
    try:
        data = api_get(oauth, url_with_type)
        return parse_players_response(data), stats_type
    except Exception:
        pass

    fallback_type = "season"
    url_fallback = (
        f"https://fantasysports.yahooapis.com/fantasy/v2/league/"
        f"{league_key}/{endpoint}/stats?format=json"
    )
    data = api_get(oauth, url_fallback)
    return parse_players_response(data), fallback_type


def fetch_stats_for_player_keys(
    oauth: OAuth2,
    league_key: str,
    player_keys: List[str],
    stats_type: str,
) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """Fetch stats for specific player keys."""
    if not player_keys:
        return {}, stats_type

    collected: Dict[str, Dict[str, Any]] = {}
    resolved_type = stats_type

    for i in range(0, len(player_keys), 25):
        chunk = player_keys[i:i + 25]
        endpoint = f"players;player_keys={','.join(chunk)}"
        players, used_type = yahoo_players_request(oauth, league_key, endpoint, resolved_type)
        resolved_type = used_type
        for row in players:
            collected[row["player_key"]] = row

    return collected, resolved_type


def fetch_free_agents(
    oauth: OAuth2,
    league_key: str,
    pool_size: int,
    stats_type: str,
) -> Tuple[List[Dict[str, Any]], str]:
    """Fetch a pool of free agents + stats."""
    out: Dict[str, Dict[str, Any]] = {}
    resolved_type = stats_type

    max_players = max(25, min(pool_size, 250))
    for start in range(0, max_players, 50):
        count = min(50, max_players - start)
        endpoint = f"players;status=FA;sort=OR;start={start};count={count}"
        players, used_type = yahoo_players_request(oauth, league_key, endpoint, resolved_type)
        resolved_type = used_type

        for row in players:
            # Keep players with at least some measurable production.
            if sum(abs(row["stats"].get(cat, 0.0)) for cat in COUNTING_CATEGORIES) <= 0.0:
                continue
            out[row["player_key"]] = row

    return list(out.values()), resolved_type


def normalize_to_per_game(stats: Dict[str, float], stats_source: str) -> Dict[str, float]:
    """Convert fetched stats to per-game values."""
    if stats_source in {"average_season", "per_game_recent_2w"}:
        return {cat: float(stats.get(cat, 0.0)) for cat in ALL_CATEGORIES}

    # Fallback for season-total stats when average_season is unavailable.
    assumed_games_played = 50.0
    per_game: Dict[str, float] = {}
    for cat in COUNTING_CATEGORIES:
        per_game[cat] = float(stats.get(cat, 0.0)) / assumed_games_played
    per_game["FG%"] = float(stats.get("FG%", 0.0))
    per_game["FT%"] = float(stats.get("FT%", 0.0))
    return per_game


def fetch_nba_games_by_team_in_range(start_day: date, end_day: date) -> Dict[str, Any]:
    """Fetch completed/scheduled NBA games by team between two dates."""
    if start_day > end_day:
        return {
            "counts": {},
            "source": "none",
            "days": 0,
            "fallback_games": 0,
        }

    counts: Dict[str, int] = defaultdict(int)
    successful_days = 0
    total_days = 0

    for day in daterange(start_day, end_day):
        total_days += 1
        ds = day.strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={ds}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            continue

        successful_days += 1
        for event in payload.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            for competitor in competitions[0].get("competitors", []):
                abbr = competitor.get("team", {}).get("abbreviation", "")
                if not abbr:
                    continue
                team = normalize_team(abbr)
                if team not in NBA_TEAM_CODES:
                    continue
                counts[team] += 1

    days = (end_day - start_day).days + 1
    fallback_games = max(1, round(days * 0.5))
    if successful_days == 0:
        return {
            "counts": {},
            "source": "fallback",
            "days": days,
            "fallback_games": fallback_games,
        }

    source = "espn" if successful_days == total_days else "espn_partial"
    return {
        "counts": dict(counts),
        "source": source,
        "days": days,
        "fallback_games": fallback_games,
    }


def recent_2w_per_game_stats(
    week_row: Optional[Dict[str, Any]],
    month_row: Optional[Dict[str, Any]],
    week_games_by_team: Dict[str, int],
    month_games_by_team: Dict[str, int],
    fallback_week_games: int,
    fallback_month_games: int,
) -> Dict[str, float]:
    """
    Approximate last-2-weeks per-game stats.

    Uses Yahoo `lastweek` and `lastmonth` totals, normalized by recent team game counts,
    then blends them (week-heavy) for recency + stability.
    """
    week_stats = (week_row or {}).get("stats", {})
    month_stats = (month_row or {}).get("stats", {})
    week_team = normalize_team((week_row or {}).get("nba_team", ""))
    month_team = normalize_team((month_row or {}).get("nba_team", ""))
    team = week_team or month_team

    week_games = max(float(week_games_by_team.get(team, fallback_week_games)), 1.0)
    month_games = max(float(month_games_by_team.get(team, fallback_month_games)), 1.0)
    has_week = bool(week_stats)
    has_month = bool(month_stats)

    if has_week and has_month:
        week_weight = 0.65
        month_weight = 0.35
    elif has_week:
        week_weight = 1.0
        month_weight = 0.0
    elif has_month:
        week_weight = 0.0
        month_weight = 1.0
    else:
        week_weight = 0.0
        month_weight = 1.0
        month_stats = {}

    out: Dict[str, float] = {}
    for cat in COUNTING_CATEGORIES:
        week_pg = (safe_float(week_stats.get(cat, 0.0)) / week_games) if has_week else 0.0
        month_pg = (safe_float(month_stats.get(cat, 0.0)) / month_games) if has_month else 0.0
        out[cat] = (week_pg * week_weight) + (month_pg * month_weight)

    week_fg = clamp(safe_float(week_stats.get("FG%", 0.45)), 0.0, 1.0)
    month_fg = clamp(safe_float(month_stats.get("FG%", 0.45)), 0.0, 1.0)
    week_ft = clamp(safe_float(week_stats.get("FT%", 0.75)), 0.0, 1.0)
    month_ft = clamp(safe_float(month_stats.get("FT%", 0.75)), 0.0, 1.0)
    out["FG%"] = clamp((week_fg * week_weight) + (month_fg * month_weight), 0.0, 1.0)
    out["FT%"] = clamp((week_ft * week_weight) + (month_ft * month_weight), 0.0, 1.0)
    return out


def fetch_remaining_nba_games(
    week_start: date,
    week_end: date,
    today: date,
) -> Dict[str, Any]:
    """
    Fetch remaining NBA games by team for the current fantasy week.

    Uses ESPN public scoreboard endpoint. If unavailable, returns a fallback.
    """
    remaining_start = max(today, week_start)
    if remaining_start > week_end:
        return {
            "counts": {},
            "source": "none",
            "days_remaining": 0,
            "fallback_games": 0,
            "remaining_dates": [],
            "teams_by_day": {},
        }

    counts: Dict[str, int] = defaultdict(int)
    teams_by_day: Dict[str, set[str]] = defaultdict(set)
    remaining_dates = [d.isoformat() for d in daterange(remaining_start, week_end)]
    successful_days = 0
    total_days = 0

    for day in daterange(remaining_start, week_end):
        total_days += 1
        day_key = day.isoformat()
        ds = day.strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={ds}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            continue

        successful_days += 1
        for event in payload.get("events", []):
            if day == today:
                status_type = event.get("status", {}).get("type", {})
                if status_type.get("completed", False):
                    continue
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            for competitor in competitions[0].get("competitors", []):
                abbr = competitor.get("team", {}).get("abbreviation", "")
                if not abbr:
                    continue
                team = normalize_team(abbr)
                if team not in NBA_TEAM_CODES:
                    continue
                counts[team] += 1
                teams_by_day[day_key].add(team)

    days_remaining = (week_end - remaining_start).days + 1
    fallback_games = max(1, round(days_remaining * 3 / 7))

    if successful_days == 0:
        return {
            "counts": {},
            "source": "fallback",
            "days_remaining": days_remaining,
            "fallback_games": fallback_games,
            "remaining_dates": remaining_dates,
            "teams_by_day": {},
        }

    source = "espn" if successful_days == total_days else "espn_partial"
    return {
        "counts": dict(counts),
        "source": source,
        "days_remaining": days_remaining,
        "fallback_games": fallback_games,
        "remaining_dates": remaining_dates,
        "teams_by_day": {d: sorted(v) for d, v in teams_by_day.items()},
    }


def player_games_remaining(
    player: Dict[str, Any],
    team_game_counts: Dict[str, int],
    fallback_games: int,
    include_news_context: bool = True,
) -> float:
    """Estimate games remaining this week for a player."""
    team = normalize_team(player.get("nba_team", ""))
    base_games = float(team_game_counts.get(team, fallback_games))
    status = (player.get("status", "") or "").strip().upper()
    mult = STATUS_MULTIPLIER.get(status, 1.0)

    if include_news_context:
        status_full = (player.get("status_full", "") or "").lower()
        news = (player.get("news", "") or "").lower()
        return_hint = (player.get("return_hint", "") or "").strip()

        # News-aware availability tweaks.
        if ("probable" in status_full or "probable" in news) and mult < 0.9:
            mult = 0.9
        elif ("questionable" in status_full or "questionable" in news) and mult < 0.75:
            mult = 0.75
        elif ("day-to-day" in status_full or "day-to-day" in news) and mult < 0.85:
            mult = 0.85

        # If Yahoo provides a return hint, avoid treating injured players as absolute zero.
        if mult == 0.0 and return_hint:
            mult = 0.25

    return max(base_games * mult, 0.0)


def project_player_contribution(
    player: Dict[str, Any],
    stats_source: str,
    team_game_counts: Dict[str, int],
    fallback_games: int,
    include_news_context: bool = True,
) -> Dict[str, Any]:
    """Project remaining-week contribution for one player."""
    per_game = normalize_to_per_game(player.get("stats", {}), stats_source)
    games = player_games_remaining(
        player,
        team_game_counts,
        fallback_games,
        include_news_context=include_news_context,
    )

    contribution = {
        "player_key": player.get("player_key", ""),
        "player_id": int(safe_float(player.get("player_id", 0))),
        "name": player.get("name", ""),
        "position": player.get("position", ""),
        "nba_team": player.get("nba_team", ""),
        "status": player.get("status", ""),
        "status_full": player.get("status_full", ""),
        "news": player.get("news", ""),
        "return_hint": player.get("return_hint", ""),
        "percent_owned": safe_float(player.get("percent_owned", 0)),
        "is_undroppable": bool(player.get("is_undroppable", False)),
        "games_remaining": round(games, 2),
    }

    for cat in COUNTING_CATEGORIES:
        contribution[cat] = per_game.get(cat, 0.0) * games

    fg_pct = clamp(per_game.get("FG%", 0.45), 0.0, 1.0)
    ft_pct = clamp(per_game.get("FT%", 0.75), 0.0, 1.0)
    pts_pg = max(per_game.get("PTS", 0.0), 0.0)

    # Yahoo response often omits FGA/FTA in this project; use a pragmatic estimate.
    fga_pg = (pts_pg * 0.55) / max(fg_pct, 0.25) if pts_pg > 0 else 0.0
    fta_pg = (pts_pg * 0.18) / max(ft_pct, 0.4) if pts_pg > 0 else 0.0

    contribution["FGM"] = fga_pg * fg_pct * games
    contribution["FGA"] = fga_pg * games
    contribution["FTM"] = fta_pg * ft_pct * games
    contribution["FTA"] = fta_pg * games

    return contribution


def project_team_totals(
    players: List[Dict[str, Any]],
    stats_source: str,
    team_game_counts: Dict[str, int],
    fallback_games: int,
    include_news_context: bool = True,
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    """Aggregate projected team totals from projected player contributions."""
    totals = {
        "3PTM": 0.0,
        "PTS": 0.0,
        "REB": 0.0,
        "AST": 0.0,
        "ST": 0.0,
        "BLK": 0.0,
        "TO": 0.0,
        "FGM": 0.0,
        "FGA": 0.0,
        "FTM": 0.0,
        "FTA": 0.0,
    }
    contributions: List[Dict[str, Any]] = []

    for player in players:
        c = project_player_contribution(
            player,
            stats_source,
            team_game_counts,
            fallback_games,
            include_news_context=include_news_context,
        )
        contributions.append(c)
        for cat in COUNTING_CATEGORIES:
            totals[cat] += c[cat]
        totals["FGM"] += c["FGM"]
        totals["FGA"] += c["FGA"]
        totals["FTM"] += c["FTM"]
        totals["FTA"] += c["FTA"]

    totals["FG%"] = totals["FGM"] / totals["FGA"] if totals["FGA"] > 0 else 0.0
    totals["FT%"] = totals["FTM"] / totals["FTA"] if totals["FTA"] > 0 else 0.0
    return totals, contributions


def player_label(player: Dict[str, Any]) -> str:
    """Render a compact player label with status when relevant."""
    name = player.get("name", "")
    status = (player.get("status", "") or "").strip().upper()
    if status in {"DTD", "GTD", "O", "INJ"}:
        return f"{name} ({status})"
    return name


def build_remaining_schedule_rows(
    my_players: List[Dict[str, Any]],
    opp_players: List[Dict[str, Any]],
    teams_by_day: Dict[str, List[str]],
    remaining_dates: List[str],
) -> List[Dict[str, Any]]:
    """Build a date-by-date list of which players on each side still play."""
    rows: List[Dict[str, Any]] = []
    for day in remaining_dates:
        teams_today = set(teams_by_day.get(day, []))
        my_today = [
            player_label(p)
            for p in my_players
            if normalize_team(p.get("nba_team", "")) in teams_today
        ]
        opp_today = [
            player_label(p)
            for p in opp_players
            if normalize_team(p.get("nba_team", "")) in teams_today
        ]
        rows.append(
            {
                "date": day,
                "my_players": sorted(my_today),
                "opp_players": sorted(opp_today),
                "my_count": len(my_today),
                "opp_count": len(opp_today),
            }
        )
    return rows


def category_win_prob(my_val: float, opp_val: float, cat: str) -> float:
    """Convert projected category edge into a win probability."""
    if cat in {"FG%", "FT%"}:
        edge = (my_val - opp_val) / 0.010
        return clamp(sigmoid(edge), 0.02, 0.98)

    if cat == "TO":
        raw = opp_val - my_val  # Lower turnovers is better.
    else:
        raw = my_val - opp_val

    scale = (max(abs(my_val), abs(opp_val), 1.0) * 0.14) + 0.4
    edge = raw / scale
    return clamp(sigmoid(edge), 0.02, 0.98)


def matchup_win_probability(category_probs: Dict[str, float], punt_categories: List[str]) -> float:
    """Dynamic-programming probability of winning a majority of categories."""
    active_probs = [p for c, p in category_probs.items() if c not in punt_categories]
    if not active_probs:
        return 0.5

    n = len(active_probs)
    need = (n // 2) + 1
    dp = [0.0] * (n + 1)
    dp[0] = 1.0

    for p in active_probs:
        nxt = [0.0] * (n + 1)
        for wins in range(0, n):
            if dp[wins] == 0.0:
                continue
            nxt[wins] += dp[wins] * (1.0 - p)
            nxt[wins + 1] += dp[wins] * p
        dp = nxt

    return sum(dp[need:])


def build_category_view(
    my_totals: Dict[str, float],
    opp_totals: Dict[str, float],
    punt_categories: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Category-level details used by API/UI."""
    view: Dict[str, Dict[str, Any]] = {}
    for cat in ALL_CATEGORIES:
        my_val = my_totals.get(cat, 0.0)
        opp_val = opp_totals.get(cat, 0.0)
        prob = category_win_prob(my_val, opp_val, cat)

        if cat == "TO":
            deterministic_winner = "YOU" if my_val < opp_val else "OPP" if opp_val < my_val else "TIE"
            margin = opp_val - my_val
        else:
            deterministic_winner = "YOU" if my_val > opp_val else "OPP" if opp_val > my_val else "TIE"
            margin = my_val - opp_val

        if cat in punt_categories:
            deterministic_winner = "PUNT"

        view[cat] = {
            "my_value": my_val,
            "opp_value": opp_val,
            "margin": margin,
            "prob_you_win": prob,
            "winner": deterministic_winner,
            "is_punt": cat in punt_categories,
        }
    return view


def drop_value_score(
    contribution: Dict[str, Any],
    include_percent_owned_guard: bool = True,
    ownership_protect_threshold: int = 80,
    respect_undroppable: bool = True,
) -> float:
    """
    All-around remaining-week value score.
    Lower values are easier drop candidates.
    """
    score = 0.0
    score += contribution.get("PTS", 0.0) * 0.60
    score += contribution.get("REB", 0.0) * 0.90
    score += contribution.get("AST", 0.0) * 1.10
    score += contribution.get("ST", 0.0) * 1.50
    score += contribution.get("BLK", 0.0) * 1.50
    score += contribution.get("3PTM", 0.0) * 0.80
    score -= contribution.get("TO", 0.0) * 0.90

    fga = contribution.get("FGA", 0.0)
    fta = contribution.get("FTA", 0.0)
    fg_pct = (contribution.get("FGM", 0.0) / fga) if fga > 0 else 0.45
    ft_pct = (contribution.get("FTM", 0.0) / fta) if fta > 0 else 0.75
    score += (fg_pct - 0.46) * fga * 0.60
    score += (ft_pct - 0.75) * fta * 0.40

    status = (contribution.get("status", "") or "").upper()
    if status in {"O", "INJ", "IR", "IL", "IL+", "NA"}:
        score -= 6.0
    elif status in {"DTD", "GTD"}:
        score -= 2.0

    if include_percent_owned_guard:
        # High percent-owned players are less realistic drop options.
        threshold = max(0, min(100, int(ownership_protect_threshold)))
        percent_owned = safe_float(contribution.get("percent_owned", 0.0))
        if percent_owned >= threshold:
            score += 4.0 + (percent_owned - threshold) * 0.45
        elif percent_owned >= max(threshold - 15, 0):
            score += 1.8

    if respect_undroppable and contribution.get("is_undroppable", False):
        score += 100.0

    return score


def summarize_category_changes(
    baseline: Dict[str, Dict[str, Any]],
    scenario: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Build category deltas and identify flips."""
    deltas: List[Dict[str, Any]] = []
    flips: List[str] = []

    for cat in ALL_CATEGORIES:
        b = baseline[cat]
        s = scenario[cat]
        delta_prob = s["prob_you_win"] - b["prob_you_win"]
        deltas.append(
            {
                "category": cat,
                "baseline_prob": b["prob_you_win"],
                "new_prob": s["prob_you_win"],
                "delta_prob": delta_prob,
                "my_before": b["my_value"],
                "my_after": s["my_value"],
                "opp_value": b["opp_value"],
            }
        )
        if b["prob_you_win"] < 0.5 <= s["prob_you_win"]:
            flips.append(cat)

    deltas.sort(key=lambda x: x["delta_prob"], reverse=True)
    return deltas, flips


def recommendation_reason(delta_rows: List[Dict[str, Any]], flips: List[str]) -> str:
    """Generate a short reason string for top recommendations."""
    improved = [row["category"] for row in delta_rows if row["delta_prob"] > 0.02][:2]
    if flips:
        return f"Flips {' + '.join(flips[:2])}; strongest gains in {' + '.join(improved or flips[:2])}."
    if improved:
        return f"Largest category-odds gains: {' + '.join(improved)}."
    return "Improves overall distribution of category outcomes."


def format_totals(totals: Dict[str, float]) -> Dict[str, float]:
    """Round totals for API payload."""
    out = {}
    for cat in ALL_CATEGORIES:
        if cat in {"FG%", "FT%"}:
            out[cat] = round(totals.get(cat, 0.0), 4)
        else:
            out[cat] = round(totals.get(cat, 0.0), 2)
    return out


def get_waiver_recommendations(
    oauth_file: str = DEFAULT_OAUTH_FILE,
    league_key: str = DEFAULT_LEAGUE_KEY,
    team_key: str = DEFAULT_TEAM_KEY,
    free_agent_pool: int = 120,
    drop_candidates: int = 4,
    max_results: int = 20,
    punt_categories: Optional[List[str]] = None,
    forced_drop_players: Optional[List[str]] = None,
    never_drop_players: Optional[List[str]] = None,
    include_current_score: bool = True,
    include_news_context: bool = True,
    include_percent_owned_guard: bool = True,
    respect_undroppable: bool = True,
    ownership_protect_threshold: int = 80,
    projection_window: str = "recent_2w",
) -> Dict[str, Any]:
    """Main API for current-matchup waiver recommendation analysis."""
    requested_projection_window = (projection_window or "recent_2w").strip().lower()
    if requested_projection_window not in PROJECTION_WINDOWS:
        requested_projection_window = "recent_2w"

    punt_categories = [
        normalize_category(cat)
        for cat in (punt_categories or [])
        if normalize_category(cat) in ALL_CATEGORIES
    ]

    oauth = ensure_oauth(oauth_file)
    gm = game.Game(oauth, "nba")
    lg = resolve_league(gm, league_key)
    resolved_team_key = resolve_team_key(lg, team_key)

    current_week = lg.current_week()
    week_start, week_end = lg.week_date_range(current_week)
    today = datetime.now().date()

    my_team = lg.to_team(resolved_team_key)
    opp_team_key = my_team.matchup(current_week)

    teams = lg.teams()
    my_team_name = teams.get(resolved_team_key, {}).get("name", "Your Team")
    opp_team_name = teams.get(opp_team_key, {}).get("name", "Opponent")

    opp_team = lg.to_team(opp_team_key)
    my_roster_raw = fetch_team_roster_current(my_team, current_week, today)
    opp_roster_raw = fetch_team_roster_current(opp_team, current_week, today)

    # Exclude explicit IL slots; keep active + bench streamable players.
    my_roster = [p for p in my_roster_raw if p.get("selected_position") not in {"IL", "IL+", "IR", "NA"}]
    opp_roster = [p for p in opp_roster_raw if p.get("selected_position") not in {"IL", "IL+", "IR", "NA"}]

    game_prefix = lg.league_id.split(".l.")[0]
    my_keys = [f"{game_prefix}.p.{p['player_id']}" for p in my_roster]
    opp_keys = [f"{game_prefix}.p.{p['player_id']}" for p in opp_roster]

    all_roster_keys = list(set(my_keys + opp_keys))
    stats_source = "average_season"
    projection_window_used = "season"
    recent_windows_info: Dict[str, Any] = {}

    roster_stats: Dict[str, Dict[str, Any]] = {}
    roster_week_stats: Dict[str, Dict[str, Any]] = {}
    roster_month_stats: Dict[str, Dict[str, Any]] = {}
    free_agents: List[Dict[str, Any]] = []
    free_week_stats: Dict[str, Dict[str, Any]] = {}
    free_month_stats: Dict[str, Dict[str, Any]] = {}
    week_game_counts: Dict[str, int] = {}
    month_game_counts: Dict[str, int] = {}
    week_fallback_games = 3
    month_fallback_games = 12

    if requested_projection_window == "recent_2w":
        roster_week_stats, roster_week_type = fetch_stats_for_player_keys(
            oauth, lg.league_id, all_roster_keys, "lastweek"
        )
        roster_month_stats, roster_month_type = fetch_stats_for_player_keys(
            oauth, lg.league_id, all_roster_keys, "lastmonth"
        )

        free_agents_month_seed, free_month_type = fetch_free_agents(
            oauth, lg.league_id, free_agent_pool, "lastmonth"
        )
        free_month_stats = {row["player_key"]: row for row in free_agents_month_seed}
        free_month_keys = list(free_month_stats.keys())
        free_week_stats, free_week_type = fetch_stats_for_player_keys(
            oauth, lg.league_id, free_month_keys, "lastweek"
        )

        recent_supported = (
            roster_week_type == "lastweek"
            and roster_month_type == "lastmonth"
            and free_month_type == "lastmonth"
            and free_week_type == "lastweek"
            and bool(free_month_stats)
        )

        if recent_supported:
            lookback_end = today - timedelta(days=1)
            if lookback_end >= (today - timedelta(days=30)):
                week_window = fetch_nba_games_by_team_in_range(
                    lookback_end - timedelta(days=6),
                    lookback_end,
                )
                month_window = fetch_nba_games_by_team_in_range(
                    lookback_end - timedelta(days=29),
                    lookback_end,
                )
                week_game_counts = week_window.get("counts", {})
                month_game_counts = month_window.get("counts", {})
                week_fallback_games = int(week_window.get("fallback_games", 3) or 3)
                month_fallback_games = int(month_window.get("fallback_games", 12) or 12)
                recent_windows_info = {
                    "week_source": week_window.get("source", "unknown"),
                    "week_days": week_window.get("days", 7),
                    "week_fallback_games": week_fallback_games,
                    "month_source": month_window.get("source", "unknown"),
                    "month_days": month_window.get("days", 30),
                    "month_fallback_games": month_fallback_games,
                }
                projection_window_used = "recent_2w"
                stats_source = "per_game_recent_2w"
            else:
                recent_supported = False

        if not recent_supported:
            roster_stats, roster_stats_source = fetch_stats_for_player_keys(
                oauth, lg.league_id, all_roster_keys, "average_season"
            )
            free_agents, free_agent_stats_source = fetch_free_agents(
                oauth, lg.league_id, free_agent_pool, "average_season"
            )
            stats_source = (
                "average_season"
                if roster_stats_source == "average_season" and free_agent_stats_source == "average_season"
                else "season"
            )
            projection_window_used = "season"
    else:
        roster_stats, roster_stats_source = fetch_stats_for_player_keys(
            oauth, lg.league_id, all_roster_keys, "average_season"
        )
        free_agents, free_agent_stats_source = fetch_free_agents(
            oauth, lg.league_id, free_agent_pool, "average_season"
        )
        stats_source = (
            "average_season"
            if roster_stats_source == "average_season" and free_agent_stats_source == "average_season"
            else "season"
        )

    my_player_ids = [int(safe_float(p.get("player_id", 0))) for p in my_roster]
    my_percent_owned_map = fetch_percent_owned_map(lg, my_player_ids)
    my_detail_signals = fetch_player_detail_signals(lg, my_player_ids)

    def roster_players(
        raw_roster: List[Dict[str, Any]],
        percent_owned_map: Optional[Dict[int, float]] = None,
        detail_signals: Optional[Dict[int, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        players: List[Dict[str, Any]] = []
        for row in raw_roster:
            player_key = f"{game_prefix}.p.{row['player_id']}"
            if projection_window_used == "recent_2w":
                week_row = roster_week_stats.get(player_key)
                month_row = roster_month_stats.get(player_key)
                pdata = month_row or week_row
                if not pdata:
                    continue
                merged = dict(pdata)
                merged["stats"] = recent_2w_per_game_stats(
                    week_row,
                    month_row,
                    week_game_counts,
                    month_game_counts,
                    week_fallback_games,
                    month_fallback_games,
                )
            else:
                pdata = roster_stats.get(player_key)
                if not pdata:
                    continue
                merged = dict(pdata)

            player_id = int(safe_float(row.get("player_id", 0)))
            signal = (detail_signals or {}).get(player_id, {})
            status = (row.get("status") or merged.get("status") or "").upper()
            merged["status"] = status
            merged["player_id"] = player_id
            merged["selected_position"] = row.get("selected_position", "")
            if percent_owned_map is not None:
                merged["percent_owned"] = safe_float(percent_owned_map.get(player_id, 0.0))
            merged["status_full"] = signal.get("status_full", "")
            merged["news"] = signal.get("news", "")
            merged["return_hint"] = signal.get("return_hint", "")
            merged["is_undroppable"] = bool(signal.get("is_undroppable", False))
            players.append(merged)
        return players

    my_players = roster_players(my_roster, my_percent_owned_map, my_detail_signals)
    opp_players = roster_players(opp_roster)

    if projection_window_used == "recent_2w":
        free_agents = []
        free_keys = sorted(set(free_month_stats.keys()) | set(free_week_stats.keys()))
        for player_key in free_keys:
            week_row = free_week_stats.get(player_key)
            month_row = free_month_stats.get(player_key)
            base = month_row or week_row
            if not base:
                continue
            merged = dict(base)
            merged["stats"] = recent_2w_per_game_stats(
                week_row,
                month_row,
                week_game_counts,
                month_game_counts,
                week_fallback_games,
                month_fallback_games,
            )
            if sum(abs(merged["stats"].get(cat, 0.0)) for cat in COUNTING_CATEGORIES) <= 0.0:
                continue
            free_agents.append(merged)

    games_info = fetch_remaining_nba_games(week_start, week_end, today)
    team_game_counts = games_info["counts"]
    fallback_games = games_info["fallback_games"]
    remaining_dates = games_info.get("remaining_dates", [])
    teams_by_day = games_info.get("teams_by_day", {})

    # Current week score as of now (already banked in matchup).
    current_my_totals = fetch_team_week_current_totals(oauth, resolved_team_key, current_week)
    current_opp_totals = fetch_team_week_current_totals(oauth, opp_team_key, current_week)

    # Remaining week projection from players/games still left.
    remaining_my_totals, my_contribs = project_team_totals(
        my_players,
        stats_source,
        team_game_counts,
        fallback_games,
        include_news_context=include_news_context,
    )
    remaining_opp_totals, _ = project_team_totals(
        opp_players,
        stats_source,
        team_game_counts,
        fallback_games,
        include_news_context=include_news_context,
    )

    # Combined end-of-week projection = current score + remaining projection.
    if include_current_score:
        baseline_my_totals = combine_totals(current_my_totals, remaining_my_totals)
        baseline_opp_totals = combine_totals(current_opp_totals, remaining_opp_totals)
        projection_basis = "current_score_plus_remaining_games"
    else:
        baseline_my_totals = dict(remaining_my_totals)
        baseline_opp_totals = dict(remaining_opp_totals)
        projection_basis = "remaining_games_only"

    baseline_categories = build_category_view(
        baseline_my_totals, baseline_opp_totals, punt_categories
    )
    current_score_categories = build_category_view(
        current_my_totals, current_opp_totals, punt_categories
    )
    baseline_category_probs = {
        cat: baseline_categories[cat]["prob_you_win"] for cat in ALL_CATEGORIES
    }
    baseline_wp = matchup_win_probability(baseline_category_probs, punt_categories)

    my_weaknesses = [
        cat
        for cat in ALL_CATEGORIES
        if cat not in punt_categories and baseline_categories[cat]["prob_you_win"] < 0.5
    ]
    my_weaknesses.sort(key=lambda c: baseline_categories[c]["prob_you_win"])

    requested_never_drop_players = [
        p.strip() for p in (never_drop_players or []) if p and p.strip()
    ]
    never_drop_keys = {p for p in requested_never_drop_players if ".p." in p}
    never_drop_names = {
        normalize_name_token(p) for p in requested_never_drop_players if ".p." not in p
    }

    drop_ranked = sorted(
        my_contribs,
        key=lambda row: drop_value_score(
            row,
            include_percent_owned_guard=include_percent_owned_guard,
            ownership_protect_threshold=ownership_protect_threshold,
            respect_undroppable=respect_undroppable,
        ),
    )
    drop_mode = "auto"
    requested_drop_players = [p.strip() for p in (forced_drop_players or []) if p and p.strip()]

    if requested_drop_players:
        drop_mode = "manual"
        contrib_by_key = {row["player_key"]: row for row in my_contribs}
        contrib_by_name: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in my_contribs:
            contrib_by_name[normalize_name_token(row.get("name", ""))].append(row)

        drop_pool: List[Dict[str, Any]] = []
        unmatched: List[str] = []
        seen_keys: set[str] = set()

        for token in requested_drop_players:
            if token in contrib_by_key:
                row = contrib_by_key[token]
                if row["player_key"] not in seen_keys:
                    drop_pool.append(row)
                    seen_keys.add(row["player_key"])
                continue

            matches = contrib_by_name.get(normalize_name_token(token), [])
            if not matches:
                unmatched.append(token)
                continue

            for row in matches:
                if row["player_key"] not in seen_keys:
                    drop_pool.append(row)
                    seen_keys.add(row["player_key"])

        if unmatched:
            raise WaiverAnalysisError(
                "Could not match selected drop player(s): " + ", ".join(unmatched)
            )
        if not drop_pool:
            raise WaiverAnalysisError("No valid selected drop players found on current roster.")
    else:
        if requested_never_drop_players:
            drop_ranked = [
                row for row in drop_ranked
                if row.get("player_key") not in never_drop_keys
                and normalize_name_token(row.get("name", "")) not in never_drop_names
            ]
            if not drop_ranked:
                raise WaiverAnalysisError(
                    "No auto-drop candidates left after applying never-drop list."
                )
        drop_pool_size = max(1, min(drop_candidates, len(drop_ranked)))
        drop_pool = drop_ranked[:drop_pool_size]

    roster_keys = {p["player_key"] for p in my_players}
    scenarios: List[Dict[str, Any]] = []

    for drop in drop_pool:
        remaining_roster = [p for p in my_players if p["player_key"] != drop["player_key"]]
        for add in free_agents:
            if add["player_key"] in roster_keys:
                continue

            scenario_players = remaining_roster + [add]
            scenario_remaining_totals, _ = project_team_totals(
                scenario_players,
                stats_source,
                team_game_counts,
                fallback_games,
                include_news_context=include_news_context,
            )
            scenario_totals = (
                combine_totals(current_my_totals, scenario_remaining_totals)
                if include_current_score
                else dict(scenario_remaining_totals)
            )
            scenario_categories = build_category_view(
                scenario_totals, baseline_opp_totals, punt_categories
            )
            scenario_probs = {
                cat: scenario_categories[cat]["prob_you_win"] for cat in ALL_CATEGORIES
            }
            scenario_wp = matchup_win_probability(scenario_probs, punt_categories)
            delta_wp = scenario_wp - baseline_wp

            deltas, flips = summarize_category_changes(baseline_categories, scenario_categories)

            scenarios.append(
                {
                    "pickup_player": add["name"],
                    "pickup_player_id": int(safe_float(add.get("player_id", 0))),
                    "pickup_team": add.get("nba_team", ""),
                    "pickup_position": add.get("position", ""),
                    "pickup_status": add.get("status", ""),
                    "pickup_games_remaining": round(
                        player_games_remaining(
                            add,
                            team_game_counts,
                            fallback_games,
                            include_news_context=include_news_context,
                        ),
                        2,
                    ),
                    "drop_player": drop["name"],
                    "drop_team": drop.get("nba_team", ""),
                    "drop_position": drop.get("position", ""),
                    "drop_status": drop.get("status", ""),
                    "drop_games_remaining": round(drop.get("games_remaining", 0.0), 2),
                    "delta_win_probability": delta_wp,
                    "new_win_probability": scenario_wp,
                    "category_deltas": deltas[:5],
                    "flipped_categories": flips,
                    "reason": recommendation_reason(deltas, flips),
                    "new_totals": format_totals(scenario_totals),
                }
            )

    scenarios.sort(
        key=lambda row: (
            row["delta_win_probability"],
            len(row["flipped_categories"]),
            row["new_win_probability"],
        ),
        reverse=True,
    )

    top = scenarios[: max_results]
    for idx, row in enumerate(top, start=1):
        row["rank"] = idx

    top_pickup_ids = [
        int(safe_float(row.get("pickup_player_id", 0)))
        for row in top
        if int(safe_float(row.get("pickup_player_id", 0))) > 0
    ]
    pickup_signals = fetch_player_detail_signals(lg, top_pickup_ids)
    for row in top:
        pid = int(safe_float(row.get("pickup_player_id", 0)))
        signal = pickup_signals.get(pid, {})
        row["pickup_news"] = signal.get("news", "")
        row["pickup_status_full"] = signal.get("status_full", "")
        row["pickup_return_hint"] = signal.get("return_hint", "")

    my_contrib_by_key = {row["player_key"]: row for row in my_contribs}
    drop_candidates_payload = []
    for row in drop_pool:
        drop_candidates_payload.append(
            {
                "name": row.get("name", ""),
                "player_key": row.get("player_key", ""),
                "player_id": row.get("player_id", 0),
                "team": row.get("nba_team", ""),
                "position": row.get("position", ""),
                "status": row.get("status", ""),
                "status_full": row.get("status_full", ""),
                "percent_owned": round(safe_float(row.get("percent_owned", 0.0)), 1),
                "is_undroppable": bool(row.get("is_undroppable", False)),
                "news": row.get("news", ""),
                "return_hint": row.get("return_hint", ""),
                "games_remaining": round(row.get("games_remaining", 0.0), 2),
                "drop_score": round(
                    drop_value_score(
                        row,
                        include_percent_owned_guard=include_percent_owned_guard,
                        ownership_protect_threshold=ownership_protect_threshold,
                        respect_undroppable=respect_undroppable,
                    ),
                    2,
                ),
                "projected": {
                    "PTS": round(row.get("PTS", 0.0), 2),
                    "REB": round(row.get("REB", 0.0), 2),
                    "AST": round(row.get("AST", 0.0), 2),
                    "ST": round(row.get("ST", 0.0), 2),
                    "BLK": round(row.get("BLK", 0.0), 2),
                    "3PTM": round(row.get("3PTM", 0.0), 2),
                    "TO": round(row.get("TO", 0.0), 2),
                },
            }
        )

    my_roster_payload = []
    for p in sorted(my_players, key=lambda row: row.get("name", "")):
        c = my_contrib_by_key.get(p.get("player_key", ""), {})
        my_roster_payload.append(
            {
                "name": p.get("name", ""),
                "player_key": p.get("player_key", ""),
                "player_id": int(safe_float(p.get("player_id", 0))),
                "team": p.get("nba_team", ""),
                "position": p.get("position", ""),
                "status": p.get("status", ""),
                "status_full": p.get("status_full", ""),
                "percent_owned": round(safe_float(p.get("percent_owned", 0.0)), 1),
                "is_undroppable": bool(p.get("is_undroppable", False)),
                "news": p.get("news", ""),
                "return_hint": p.get("return_hint", ""),
                "games_remaining": round(c.get("games_remaining", 0.0), 2),
                "drop_score": (
                    round(
                        drop_value_score(
                            c,
                            include_percent_owned_guard=include_percent_owned_guard,
                            ownership_protect_threshold=ownership_protect_threshold,
                            respect_undroppable=respect_undroppable,
                        ),
                        2,
                    )
                    if c
                    else 0.0
                ),
            }
        )

    schedule_rows = build_remaining_schedule_rows(
        my_players,
        opp_players,
        teams_by_day,
        remaining_dates,
    )

    return {
        "success": True,
        "data": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "league_key": lg.league_id,
            "my_team_key": resolved_team_key,
            "my_team_name": my_team_name,
            "opponent_team_key": opp_team_key,
            "opponent_team_name": opp_team_name,
            "available_teams": [
                {
                    "team_key": key,
                    "team_name": meta.get("name", key),
                }
                for key, meta in sorted(
                    teams.items(),
                    key=lambda row: (row[1].get("name", ""), row[0]),
                )
            ],
            "current_week": current_week,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "today": today.isoformat(),
            "games_source": games_info["source"],
            "days_remaining": games_info.get("days_remaining", 0),
            "games_remaining_by_team": team_game_counts,
            "stats_source": stats_source,
            "projection_window_requested": requested_projection_window,
            "projection_window_used": projection_window_used,
            "recent_windows_info": recent_windows_info,
            "projection_basis": projection_basis,
            "settings": {
                "free_agent_pool": free_agent_pool,
                "drop_candidates": drop_candidates,
                "max_results": max_results,
                "projection_window_requested": requested_projection_window,
                "projection_window_used": projection_window_used,
                "selected_team_key": resolved_team_key,
                "punt_categories": punt_categories,
                "drop_mode": drop_mode,
                "requested_drop_players": requested_drop_players,
                "requested_never_drop_players": requested_never_drop_players,
                "include_current_score": include_current_score,
                "include_news_context": include_news_context,
                "include_percent_owned_guard": include_percent_owned_guard,
                "respect_undroppable": respect_undroppable,
                "ownership_protect_threshold": ownership_protect_threshold,
            },
            "my_roster": my_roster_payload,
            "current_score": {
                "my_totals": format_totals(current_my_totals),
                "opp_totals": format_totals(current_opp_totals),
                "categories": {
                    cat: {
                        "my_value": round(current_score_categories[cat]["my_value"], 4),
                        "opp_value": round(current_score_categories[cat]["opp_value"], 4),
                        "margin": round(current_score_categories[cat]["margin"], 4),
                        "winner": current_score_categories[cat]["winner"],
                        "is_punt": current_score_categories[cat]["is_punt"],
                    }
                    for cat in ALL_CATEGORIES
                },
                "deterministic_score": {
                    "wins": sum(
                        1
                        for cat in ALL_CATEGORIES
                        if cat not in punt_categories
                        and current_score_categories[cat]["winner"] == "YOU"
                    ),
                    "losses": sum(
                        1
                        for cat in ALL_CATEGORIES
                        if cat not in punt_categories
                        and current_score_categories[cat]["winner"] == "OPP"
                    ),
                    "ties": sum(
                        1
                        for cat in ALL_CATEGORIES
                        if cat not in punt_categories
                        and current_score_categories[cat]["winner"] == "TIE"
                    ),
                },
            },
            "remaining_schedule": {
                "dates": remaining_dates,
                "rows": schedule_rows,
                "source": games_info["source"],
            },
            "baseline": {
                "win_probability": baseline_wp,
                "expected_categories_won": sum(
                    baseline_category_probs[cat]
                    for cat in ALL_CATEGORIES
                    if cat not in punt_categories
                ),
                "deterministic_score": {
                    "wins": sum(
                        1
                        for cat in ALL_CATEGORIES
                        if cat not in punt_categories
                        and baseline_categories[cat]["prob_you_win"] >= 0.5
                    ),
                    "losses": sum(
                        1
                        for cat in ALL_CATEGORIES
                        if cat not in punt_categories
                        and baseline_categories[cat]["prob_you_win"] < 0.5
                    ),
                },
                "my_totals": format_totals(baseline_my_totals),
                "opp_totals": format_totals(baseline_opp_totals),
                "categories": {
                    cat: {
                        "my_value": round(baseline_categories[cat]["my_value"], 4),
                        "opp_value": round(baseline_categories[cat]["opp_value"], 4),
                        "margin": round(baseline_categories[cat]["margin"], 4),
                        "prob_you_win": baseline_categories[cat]["prob_you_win"],
                        "winner": baseline_categories[cat]["winner"],
                        "is_punt": baseline_categories[cat]["is_punt"],
                    }
                    for cat in ALL_CATEGORIES
                },
                "my_weaknesses": my_weaknesses,
                "opponent_strengths": my_weaknesses[:],
            },
            "drop_candidates": drop_candidates_payload,
            "recommendations": top,
        },
    }


def get_waiver_team_options(
    oauth_file: str = DEFAULT_OAUTH_FILE,
    league_key: str = DEFAULT_LEAGUE_KEY,
    preferred_team_key: str = DEFAULT_TEAM_KEY,
) -> Dict[str, Any]:
    """Return league team choices for UI selection."""
    oauth = ensure_oauth(oauth_file)
    gm = game.Game(oauth, "nba")
    lg = resolve_league(gm, league_key)
    selected_team_key = resolve_team_key(lg, preferred_team_key)
    teams = lg.teams()

    options = [
        {
            "team_key": team_key,
            "team_name": meta.get("name", team_key),
        }
        for team_key, meta in sorted(
            teams.items(),
            key=lambda row: (row[1].get("name", ""), row[0]),
        )
    ]

    return {
        "success": True,
        "data": {
            "league_key": lg.league_id,
            "selected_team_key": selected_team_key,
            "teams": options,
        },
    }
