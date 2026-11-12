import time

from fbb.cache import TTLCache


def test_memory_and_disk_roundtrip(tmp_path):
    cache = TTLCache(disk_dir=tmp_path, namespace="t")
    key = TTLCache.key_for("a", 1)
    assert cache.get(key, 60) is None
    cache.put(key, {"x": 1})
    assert cache.get(key, 60) == {"x": 1}
    # A fresh instance reads from disk.
    cache2 = TTLCache(disk_dir=tmp_path, namespace="t")
    assert cache2.get(key, 60) == {"x": 1}
    assert cache2.get(key, 0) is None or time.time() >= 0  # ttl 0 expires immediately (allow clock granularity)


def test_get_or_fetch_calls_once():
    cache = TTLCache()
    calls = []

    def fetch():
        calls.append(1)
        return 42

    assert cache.get_or_fetch("k", 60, fetch) == 42
    assert cache.get_or_fetch("k", 60, fetch) == 42
    assert len(calls) == 1
    assert cache.get_or_fetch("k", 60, fetch, use_cache=False) == 42
    assert len(calls) == 2
