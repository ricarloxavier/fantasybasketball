"""Parsing of Yahoo Fantasy JSON payloads into plain dicts."""

from __future__ import annotations

from typing import Any

from ..categories import ALL_CATEGORIES, COMPOSITE_STAT_MAP, STAT_MAP, VOLUME_STAT_MAP, normalize_category
from ..util import safe_float, safe_int, walk_values


def _apply_stat(stats: dict[str, float], stat_id: str, value: Any, display_name: str = "") -> None:
    if stat_id in STAT_MAP:
        stats[STAT_MAP[stat_id]] = safe_float(value)
        return
    if stat_id in COMPOSITE_STAT_MAP:
        made_key, att_key = COMPOSITE_STAT_MAP[stat_id]
        text = str(value or "")
        if "/" in text:
            made, att = text.split("/", 1)
            stats[made_key] = safe_float(made)
            stats[att_key] = safe_float(att)
        return
    if stat_id in VOLUME_STAT_MAP:
        stats[VOLUME_STAT_MAP[stat_id]] = safe_float(value)
        return
    display = normalize_category(display_name)
    if display in ALL_CATEGORIES and display not in stats:
        stats[display] = safe_float(value)


def extract_category_stats(payload: Any) -> dict[str, float]:
    """Pull mapped category (and volume) values from any Yahoo stats payload."""
    stats: dict[str, float] = {}
    for stat_obj in walk_values(payload, "stat"):
        if not isinstance(stat_obj, dict) or "value" not in stat_obj:
            continue
        _apply_stat(stats, str(stat_obj.get("stat_id", "")), stat_obj.get("value"), str(stat_obj.get("display_name", "")))
    return stats


def parse_player_node(player_node: list[Any]) -> dict[str, Any]:
    """Parse one `player` node (metadata list + optional stats block)."""
    meta: dict[str, Any] = {
        "player_key": "",
        "player_id": 0,
        "name": "",
        "position": "",
        "nba_team": "",
        "status": "",
        "status_full": "",
        "injury_note": "",
        "percent_owned": None,
        "eligible_positions": [],
        "stats": {},
    }
    meta_block = player_node[0] if player_node else []
    for item in meta_block:
        if not isinstance(item, dict):
            continue
        if "player_key" in item:
            meta["player_key"] = item["player_key"]
        elif "player_id" in item:
            meta["player_id"] = safe_int(item["player_id"])
        elif "name" in item and isinstance(item["name"], dict):
            meta["name"] = item["name"].get("full", "")
        elif "display_position" in item:
            meta["position"] = item["display_position"]
        elif "editorial_team_abbr" in item:
            meta["nba_team"] = item["editorial_team_abbr"]
        elif "status" in item and isinstance(item["status"], str):
            meta["status"] = item["status"].strip().upper()
        elif "status_full" in item and isinstance(item["status_full"], str):
            meta["status_full"] = item["status_full"].strip()
        elif "injury_note" in item and isinstance(item["injury_note"], str):
            meta["injury_note"] = item["injury_note"].strip()
        elif "eligible_positions" in item and isinstance(item["eligible_positions"], list):
            meta["eligible_positions"] = [p.get("position") for p in item["eligible_positions"] if isinstance(p, dict)]

    for block in player_node[1:]:
        if not isinstance(block, dict):
            continue
        stats_block = block.get("player_stats")
        if isinstance(stats_block, dict):
            for row in stats_block.get("stats", []):
                stat = row.get("stat", {}) if isinstance(row, dict) else {}
                _apply_stat(meta["stats"], str(stat.get("stat_id", "")), stat.get("value"))
        owned = block.get("percent_owned")
        if isinstance(owned, list):
            for item in owned:
                if isinstance(item, dict) and "value" in item:
                    meta["percent_owned"] = safe_float(item["value"])
    return meta


def parse_players_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Players from a `league/.../players...` response."""
    league_section = data.get("fantasy_content", {}).get("league", [])
    if len(league_section) < 2:
        return []
    return _parse_players_block(league_section[1].get("players", {}))


def parse_team_roster_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Players from a `team/.../roster/players` response, with selected_position."""
    team_section = data.get("fantasy_content", {}).get("team", [])
    if len(team_section) < 2:
        return []
    roster = team_section[1].get("roster", {})
    players_block = roster.get("0", {}).get("players", {}) if isinstance(roster, dict) else {}
    out = _parse_players_block(players_block)
    return out


def _parse_players_block(players_block: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, wrapped in players_block.items():
        if key == "count" or not isinstance(wrapped, dict):
            continue
        node = wrapped.get("player", [])
        if not node:
            continue
        parsed = parse_player_node(node)
        for block in node[1:]:
            if isinstance(block, dict) and "selected_position" in block:
                sel = block["selected_position"]
                if isinstance(sel, list):
                    for item in sel:
                        if isinstance(item, dict) and "position" in item:
                            parsed["selected_position"] = item["position"]
        if parsed["player_key"]:
            out.append(parsed)
    return out


def parse_teams_response(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{team_key: {"name", "is_mine", "team_id"}} from a `league/.../teams` response."""
    league_section = data.get("fantasy_content", {}).get("league", [])
    if len(league_section) < 2:
        return {}
    teams = league_section[1].get("teams", {})
    out: dict[str, dict[str, Any]] = {}
    for key, wrapped in teams.items():
        if key == "count" or not isinstance(wrapped, dict):
            continue
        node = wrapped.get("team", [])
        meta = node[0] if node else []
        info: dict[str, Any] = {"name": "", "is_mine": False, "team_id": 0}
        team_key = ""
        for item in meta:
            if not isinstance(item, dict):
                continue
            if "team_key" in item:
                team_key = item["team_key"]
            elif "team_id" in item:
                info["team_id"] = safe_int(item["team_id"])
            elif "name" in item:
                info["name"] = item["name"]
            elif "is_owned_by_current_login" in item:
                info["is_mine"] = str(item["is_owned_by_current_login"]) in {"1", "True", "true"}
        if team_key:
            out[team_key] = info
    return out


def parse_league_meta(data: dict[str, Any]) -> dict[str, Any]:
    league_section = data.get("fantasy_content", {}).get("league", [])
    meta = league_section[0] if league_section and isinstance(league_section[0], dict) else {}
    return {
        "league_key": meta.get("league_key", ""),
        "league_id": str(meta.get("league_id", "")),
        "name": meta.get("name", ""),
        "season": str(meta.get("season", "")),
        "current_week": safe_int(meta.get("current_week"), 1),
        "start_week": safe_int(meta.get("start_week"), 1),
        "end_week": safe_int(meta.get("end_week"), 1),
        "start_date": meta.get("start_date", ""),
        "end_date": meta.get("end_date", ""),
        "num_teams": safe_int(meta.get("num_teams")),
        "scoring_type": meta.get("scoring_type", ""),
    }
