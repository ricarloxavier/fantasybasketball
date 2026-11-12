"""Waiver page preferences: one schema used for saved prefs and query-string overrides."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

from .categories import parse_punt_categories
from .optimizer import WaiverOptions
from .util import parse_bool

_LOCK = threading.Lock()

DEFAULT_PREFS: dict[str, Any] = {
    "selected_team_key": "",
    "projection_window": "recent_2w",
    "free_agent_pool": 120,
    "drop_candidates": 4,
    "max_results": 20,
    "punt_categories": [],
    "selected_drop_players": [],
    "selected_never_drop_players": [],
    "drop_player_text": "",
    "never_drop_text": "",
    "include_current_score": True,
    "include_news_context": True,
    "include_percent_owned_guard": True,
    "respect_undroppable": True,
    "ownership_protect_threshold": 80,
}
LIMITS = {"free_agent_pool": (25, 250), "drop_candidates": (1, 8), "max_results": (5, 50), "ownership_protect_threshold": (0, 100)}
BOOL_KEYS = ("include_current_score", "include_news_context", "include_percent_owned_guard", "respect_undroppable")
LIST_KEYS = ("selected_drop_players", "selected_never_drop_players")


def clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out[:40]


def sanitize(payload: Mapping[str, Any], base: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Validate `payload`; keys absent from it keep the value from `base` (or the default)."""
    base = dict(base or DEFAULT_PREFS)
    out = dict(base)
    for key in payload or {}:
        if key not in DEFAULT_PREFS:
            continue
        value = payload[key]
        if key in LIMITS:
            out[key] = clamp_int(value, base[key], *LIMITS[key])
        elif key in BOOL_KEYS:
            out[key] = parse_bool(value, base[key])
        elif key == "punt_categories":
            out[key] = parse_punt_categories(value)
        elif key in LIST_KEYS:
            out[key] = _as_list(value)
        elif key == "projection_window":
            text = str(value or "").strip().lower()
            out[key] = text if text in {"recent_2w", "season"} else base[key]
        elif key == "selected_team_key":
            out[key] = str(value or "").strip()[:80]
        else:
            out[key] = str(value or "")[:300]
    return out


def to_options(prefs: Mapping[str, Any]) -> WaiverOptions:
    drops = list(prefs.get("selected_drop_players", [])) + _as_list(prefs.get("drop_player_text", ""))
    never = list(prefs.get("selected_never_drop_players", [])) + _as_list(prefs.get("never_drop_text", ""))
    return WaiverOptions(
        free_agent_pool=int(prefs["free_agent_pool"]), drop_candidates=int(prefs["drop_candidates"]), max_results=int(prefs["max_results"]),
        punt_categories=list(prefs.get("punt_categories", [])), forced_drop_players=drops, never_drop_players=never,
        include_current_score=bool(prefs["include_current_score"]), include_news_context=bool(prefs["include_news_context"]),
        include_percent_owned_guard=bool(prefs["include_percent_owned_guard"]), respect_undroppable=bool(prefs["respect_undroppable"]),
        ownership_protect_threshold=int(prefs["ownership_protect_threshold"]), projection_window=str(prefs["projection_window"]),
    )


QUERY_ALIASES = {"punt": "punt_categories", "drop_player": "selected_drop_players", "never_drop": "selected_never_drop_players",
                 "team_key": "selected_team_key"}


def apply_query(prefs: Mapping[str, Any], args: Mapping[str, str]) -> dict[str, Any]:
    """Overlay URL query parameters (with their legacy aliases) on saved prefs."""
    payload: dict[str, Any] = {}
    for key, value in args.items():
        target = QUERY_ALIASES.get(key, key)
        if target in DEFAULT_PREFS:
            payload[target] = value
    if "selected_drop_players" in payload:
        payload["drop_player_text"] = ""
    if "selected_never_drop_players" in payload:
        payload["never_drop_text"] = ""
    return sanitize(payload, prefs)


class PrefsStore:
    """JSON file of prefs per username, written atomically."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, user: str) -> dict[str, Any]:
        saved = self._read().get(user, {})
        return sanitize(saved if isinstance(saved, dict) else {}, DEFAULT_PREFS)

    def update(self, user: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with _LOCK:
            store = self._read()
            updated = sanitize(payload, self.get(user))
            store[user] = updated
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(store, indent=2, sort_keys=True), "utf-8")
            os.replace(tmp, self.path)
        return updated
