"""Most added / most dropped players from the league's add/drop transactions."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .util import safe_int, walk_values


def tally_adds_drops(transactions: list[Any]) -> dict[str, Counter]:
    adds: Counter = Counter()
    drops: Counter = Counter()
    for tx in transactions:
        players = None
        for p in walk_values(tx, "players"):
            players = p
            break
        if not isinstance(players, dict):
            continue
        for i in range(safe_int(players.get("count", 0))):
            node = players.get(str(i), {}).get("player")
            if not isinstance(node, list) or len(node) < 2:
                continue
            name = next((item["name"].get("full", "") for item in node[0] if isinstance(item, dict) and "name" in item), "")
            data = node[1].get("transaction_data", {}) if isinstance(node[1], dict) else {}
            if isinstance(data, list):
                data = data[0] if data else {}
            kind = data.get("type", "")
            if not name:
                continue
            if kind == "add":
                adds[name] += 1
            elif kind == "drop":
                drops[name] += 1
    return {"adds": adds, "drops": drops}


def trends(adds: Counter, drops: Counter, min_moves: int = 2) -> dict[str, list[dict[str, Any]]]:
    """Players added more than dropped (hot) and dropped more than added (cold)."""
    hot, cold = [], []
    for name, a in adds.items():
        d = drops.get(name, 0)
        if a >= min_moves:
            hot.append({"player": name, "adds": a, "drops": d, "net": a - d, "ratio": round(a / max(d, 1), 2)})
    for name, d in drops.items():
        a = adds.get(name, 0)
        if d >= min_moves:
            cold.append({"player": name, "adds": a, "drops": d, "net": d - a, "ratio": round(d / max(a, 1), 2)})
    hot.sort(key=lambda r: (r["net"], r["ratio"]), reverse=True)
    cold.sort(key=lambda r: (r["net"], r["ratio"]), reverse=True)
    return {"hot": hot, "cold": cold}


def add_events(transactions: list[Any]) -> list[dict[str, Any]]:
    """One row per added player: when, who, which fantasy team, the player's NBA team."""
    out: list[dict[str, Any]] = []
    for tx in transactions:
        meta = tx[0] if isinstance(tx, list) else tx
        ts = safe_int(meta.get("timestamp", 0)) if isinstance(meta, dict) else 0
        players = next((p for p in walk_values(tx, "players")), None)
        if not isinstance(players, dict):
            continue
        for i in range(safe_int(players.get("count", 0))):
            node = players.get(str(i), {}).get("player")
            if not isinstance(node, list) or len(node) < 2:
                continue
            info: dict[str, Any] = {}
            for item in node[0]:
                if isinstance(item, dict):
                    if "name" in item:
                        info["name"] = item["name"].get("full", "")
                    if "editorial_team_abbr" in item:
                        info["nba_team"] = item["editorial_team_abbr"]
            data = node[1].get("transaction_data", {}) if isinstance(node[1], dict) else {}
            if isinstance(data, list):
                data = data[0] if data else {}
            if data.get("type") == "add":
                out.append({"timestamp": ts, "player": info.get("name", ""), "nba_team": info.get("nba_team", ""),
                            "fantasy_team": data.get("destination_team_name", ""), "source": data.get("source_type", "")})
    return out


def same_day_adds(adds: list[dict[str, Any]], schedule, tz: str = "America/New_York") -> dict[str, Any]:
    """For each add, did the player's NBA team play that calendar day (league time)?

    Returns per-fantasy-team counts of same-day vs. no-game adds plus the detailed rows,
    using the ESPN schedule (`fbb.schedule.NbaSchedule`).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from .categories import normalize_team
    from .util import today_in_tz

    zone = ZoneInfo(tz)
    today = today_in_tz(tz)
    by_day: dict[str, set[str]] = {}
    rows: list[dict[str, Any]] = []
    for add in adds:
        if not add["timestamp"]:
            continue
        day = datetime.fromtimestamp(add["timestamp"], zone).date()
        key = day.isoformat()
        if key not in by_day:
            games = schedule.games_on(day, today) or []
            by_day[key] = {t for g in games for t in (g["home"], g["away"])}
        team = normalize_team(add["nba_team"])
        rows.append({**add, "date": key, "plays_that_day": team in by_day[key]})
    summary: dict[str, dict[str, int]] = {}
    for r in rows:
        s = summary.setdefault(r["fantasy_team"], {"adds": 0, "same_day": 0, "no_game": 0})
        s["adds"] += 1
        s["same_day" if r["plays_that_day"] else "no_game"] += 1
    return {"rows": rows, "by_team": summary}
