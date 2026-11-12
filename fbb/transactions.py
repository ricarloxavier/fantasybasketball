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
