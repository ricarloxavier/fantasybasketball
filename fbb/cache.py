"""A small TTL cache with an in-memory layer and an optional disk layer.

Used for ESPN scoreboard days, Hashtag pages and awards results so repeated
questions in one session, or two web requests in a row, do not refetch.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional


class TTLCache:
    def __init__(self, disk_dir: Optional[Path] = None, namespace: str = "default"):
        self._mem: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.disk_dir = (Path(disk_dir) / namespace) if disk_dir else None
        if self.disk_dir:
            self.disk_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key_for(*parts: Any) -> str:
        raw = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _disk_path(self, key: str) -> Optional[Path]:
        return (self.disk_dir / f"{key}.json") if self.disk_dir else None

    def get(self, key: str, ttl_seconds: float) -> Optional[Any]:
        now = time.time()
        with self._lock:
            hit = self._mem.get(key)
            if hit and now - hit[0] <= ttl_seconds:
                return hit[1]
        path = self._disk_path(key)
        if path and path.exists():
            try:
                stored = json.loads(path.read_text("utf-8"))
                if now - float(stored.get("t", 0)) <= ttl_seconds:
                    with self._lock:
                        self._mem[key] = (float(stored["t"]), stored["v"])
                    return stored["v"]
            except (ValueError, KeyError, OSError):
                return None
        return None

    def put(self, key: str, value: Any) -> None:
        t = time.time()
        with self._lock:
            self._mem[key] = (t, value)
        path = self._disk_path(key)
        if path:
            tmp = path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps({"t": t, "v": value}, default=str), "utf-8")
                tmp.replace(path)
            except (TypeError, OSError):
                tmp.unlink(missing_ok=True)

    def get_or_fetch(self, key: str, ttl_seconds: float, fetch: Callable[[], Any], use_cache: bool = True) -> Any:
        if use_cache:
            hit = self.get(key, ttl_seconds)
            if hit is not None:
                return hit
        value = fetch()
        self.put(key, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
        if self.disk_dir:
            for p in self.disk_dir.glob("*.json"):
                p.unlink(missing_ok=True)
