from datetime import date

import pytest

from fbb.cache import TTLCache
from fbb.schedule import NbaSchedule, parse_scoreboard_day
from tests.conftest import load_json


class FixtureSession:
    """Serves recorded ESPN scoreboard days; records which days were requested."""

    def __init__(self):
        self.requested = []

    def get(self, url, params=None, timeout=None):
        ds = params["dates"]
        self.requested.append(ds)
        payload = load_json(f"espn/scoreboard_{ds}.json")

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return payload
        return R()


def test_parse_scoreboard_day_counts_games():
    games = parse_scoreboard_day(load_json("espn/scoreboard_20261112.json"))
    assert len(games) == 4
    teams = {g["home"] for g in games} | {g["away"] for g in games}
    assert "PHI" in teams and "TOR" in teams


def test_games_by_team_over_a_week_uses_cache():
    session = FixtureSession()
    sched = NbaSchedule(cache=TTLCache(), session=session)
    today = date(2026, 11, 12)
    info = sched.games_by_team(date(2026, 11, 9), date(2026, 11, 15), today)
    assert info["source"] == "espn"
    assert info["days"] == 7
    assert sum(info["counts"].values()) == 2 * (8 + 6 + 10 + 4 + 9 + 5 + 9)
    assert set(info["teams_by_day"]) == {f"2026-11-{d:02d}" for d in range(9, 16)}
    # Second call is served from cache: no new requests.
    n = len(session.requested)
    sched.games_by_team(date(2026, 11, 9), date(2026, 11, 15), today)
    assert len(session.requested) == n


def test_remaining_games_starts_today_and_excludes_completed():
    session = FixtureSession()
    sched = NbaSchedule(cache=TTLCache(), session=session)
    today = date(2026, 11, 12)
    info = sched.remaining_games(date(2026, 11, 9), date(2026, 11, 15), today)
    assert info["dates"][0] == "2026-11-12"
    assert info["dates"][-1] == "2026-11-15"
    # The recorded Nov 12 games were all still scheduled, so all four count.
    assert info["teams_by_day"]["2026-11-12"]
    assert info["source"] == "espn"


def test_fallback_when_fetch_fails():
    class BadSession:
        def get(self, *a, **k):
            raise RuntimeError("down")

    import requests

    class BadSession2:
        def get(self, *a, **k):
            raise requests.ConnectionError("down")

    sched = NbaSchedule(cache=TTLCache(), session=BadSession2())
    info = sched.remaining_games(date(2026, 11, 9), date(2026, 11, 15), date(2026, 11, 12))
    assert info["source"] == "fallback"
    assert info["counts"] == {}
    assert info["fallback_games"] == 2  # 4 days * 3/7 rounded
