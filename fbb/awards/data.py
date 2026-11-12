"""Putilla awards data layer: transactions -> roster stints -> prorated value.

Pure functions take transaction dicts (as Yahoo returns them) and stats; the
only network call is `fetch_season_stats`, which goes through YahooClient in
pages of 25 instead of one request per player.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from ..categories import PERCENT_CATEGORIES
from ..util import safe_float, safe_int, walk_values
from ..yahoo.client import YahooClient

log = logging.getLogger(__name__)

ADD_SOURCES = {"waivers", "freeagents"}

# Fantasy value per unit of each counting stat (rare stats are worth more).
VALUE_WEIGHTS = {"PTS": 0.03, "REB": 0.06, "AST": 0.08, "ST": 0.35, "BLK": 0.35, "3PTM": 0.12, "TO": -0.10}
MIN_POINTS_TO_QUALIFY = 100
STRONG_THRESHOLDS = {"PTS": 200, "REB": 75, "AST": 50, "ST": 15, "BLK": 12, "3PTM": 25}
ELITE_BONUS, SOLID_BONUS = 1.15, 1.08
DISQUALIFIED = -999


def _first(obj: Any, key: str) -> Any:
    for v in walk_values(obj, key):
        return v
    return None


def build_roster_timeline(transactions: list[Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Pair add and drop events per player into roster stints; count drops per player."""
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    drop_counts: dict[str, int] = defaultdict(int)

    for tx in transactions:
        meta = tx[0] if isinstance(tx, list) else tx
        timestamp = safe_int(meta.get("timestamp", 0)) if isinstance(meta, dict) else 0
        players = _first(tx, "players")
        if not isinstance(players, dict):
            continue
        for i in range(safe_int(players.get("count", 0))):
            node = players.get(str(i), {}).get("player")
            if not isinstance(node, list) or not node:
                continue
            info: dict[str, Any] = {}
            for item in node[0]:
                if not isinstance(item, dict):
                    continue
                if "name" in item:
                    info["name"] = item["name"].get("full", "Unknown")
                if "player_key" in item:
                    info["player_key"] = item["player_key"]
                if "editorial_team_abbr" in item:
                    info["nba_team"] = item["editorial_team_abbr"]
                if "display_position" in item:
                    info["position"] = item["display_position"]
            if len(node) < 2:
                continue
            tx_data = node[1].get("transaction_data", {}) if isinstance(node[1], dict) else {}
            if isinstance(tx_data, list):
                tx_data = tx_data[0] if tx_data else {}
            action = tx_data.get("type", "")
            name = info.get("name", "Unknown")
            if action == "add" and tx_data.get("source_type", "") in ADD_SOURCES:
                events[name].append({
                    "type": "add", "timestamp": timestamp, "team_key": tx_data.get("destination_team_key", ""),
                    "team_name": tx_data.get("destination_team_name", ""), "player_key": info.get("player_key"),
                    "nba_team": info.get("nba_team", ""), "position": info.get("position", ""), "source": tx_data.get("source_type", ""),
                })
            elif action == "drop":
                drop_counts[name] += 1
                events[name].append({
                    "type": "drop", "timestamp": timestamp, "team_key": tx_data.get("source_team_key", ""),
                    "team_name": tx_data.get("source_team_name", ""), "player_key": info.get("player_key"),
                })

    stints: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for name, evs in events.items():
        evs.sort(key=lambda e: e["timestamp"])
        active: dict[str, dict[str, Any]] = {}
        for ev in evs:
            if ev["type"] == "add":
                active[ev["team_key"]] = {
                    "player_name": name, "player_key": ev.get("player_key"), "team_key": ev["team_key"], "team_name": ev["team_name"],
                    "nba_team": ev.get("nba_team", ""), "position": ev.get("position", ""), "add_timestamp": ev["timestamp"],
                    "drop_timestamp": None, "source": ev.get("source", ""),
                }
            elif ev["team_key"] in active:
                stint = active.pop(ev["team_key"])
                stint["drop_timestamp"] = ev["timestamp"]
                stints[name].append(stint)
        for stint in active.values():
            stints[name].append(stint)
    return dict(stints), dict(drop_counts)


def season_week_origin(start_date: str) -> date:
    """Monday on or before the league start date; fantasy weeks run Monday to Sunday."""
    start = date.fromisoformat(start_date)
    return start - timedelta(days=start.weekday())


def timestamp_to_week(timestamp: int, week_origin: date, max_week: int, tz: str = "America/New_York") -> int:
    event_day = datetime.fromtimestamp(timestamp, ZoneInfo(tz)).date()
    week = max(1, (event_day - week_origin).days // 7 + 1)
    return min(week, max_week)


def prorate_stint(stint: Mapping[str, Any], season_stats: Mapping[str, float], current_week: int, week_origin: date,
                  max_week: int, tz: str = "America/New_York") -> dict[str, float]:
    """Scale season totals by the share of the season the player spent on this roster."""
    start_week = timestamp_to_week(safe_int(stint.get("add_timestamp", 0)), week_origin, max_week, tz)
    end_week = timestamp_to_week(stint["drop_timestamp"], week_origin, max_week, tz) if stint.get("drop_timestamp") else current_week
    weeks = min(max(1, end_week - start_week + 1), current_week)
    factor = weeks / max(current_week, 1)
    out = {cat: (value if cat in PERCENT_CATEGORIES else value * factor) for cat, value in season_stats.items()}
    out["weeks"] = weeks
    out["prorate_factor"] = factor
    return out


def player_value(stats: Mapping[str, float]) -> tuple[float, dict[str, float]]:
    """Fantasy value of a (prorated) stat line; DISQUALIFIED below the points floor."""
    pts = stats.get("PTS", 0)
    if pts < MIN_POINTS_TO_QUALIFY:
        return DISQUALIFIED, {}
    values = {cat: stats.get(cat, 0) * w for cat, w in VALUE_WEIGHTS.items()}
    total = sum(values.values())
    strong = sum(1 for cat, threshold in STRONG_THRESHOLDS.items() if stats.get(cat, 0) >= threshold)
    if strong >= 5:
        total *= ELITE_BONUS
    elif strong >= 4:
        total *= SOLID_BONUS
    return total, values


def fetch_season_stats(client: YahooClient, player_keys: list[str]) -> dict[str, dict[str, float]]:
    rows = client.player_stats(player_keys, "season")
    return {key: row["stats"] for key, row in rows.items() if row.get("stats")}


def analyze_stints(stints: Mapping[str, list[dict[str, Any]]], season_stats: Mapping[str, Mapping[str, float]], current_week: int,
                   week_origin: date, max_week: int, drop_counts: Optional[Mapping[str, int]] = None,
                   tz: str = "America/New_York") -> list[dict[str, Any]]:
    """Value every stint that has stats and clears the points floor, best first."""
    drop_counts = drop_counts or {}
    out: list[dict[str, Any]] = []
    for name, rows in stints.items():
        for stint in rows:
            key = stint.get("player_key")
            if key not in season_stats:
                continue
            prorated = prorate_stint(stint, season_stats[key], current_week, week_origin, max_week, tz)
            if prorated.get("weeks", 0) <= 0:
                continue
            value, cat_values = player_value(prorated)
            if value <= DISQUALIFIED:
                continue
            zone = ZoneInfo(tz)
            add_date = datetime.fromtimestamp(safe_int(stint.get("add_timestamp", 0)), zone).strftime("%m/%d")
            drop_date = "now" if not stint.get("drop_timestamp") else datetime.fromtimestamp(stint["drop_timestamp"], zone).strftime("%m/%d")
            weeks = int(prorated["weeks"])
            out.append({
                "player_name": name, "player_key": key, "team_name": stint.get("team_name", "Unknown"), "team_key": stint.get("team_key"),
                "nba_team": stint.get("nba_team", ""), "position": stint.get("position", ""), "add_date": add_date, "drop_date": drop_date,
                "weeks": weeks, "stats": prorated, "total_value": value, "category_values": cat_values,
                "still_rostered": stint.get("drop_timestamp") is None, "drop_count": drop_counts.get(name, 0),
                "value_per_game": value / weeks if weeks else 0.0,
            })
    out.sort(key=lambda s: s["total_value"], reverse=True)
    return out
