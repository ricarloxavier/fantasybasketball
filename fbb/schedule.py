"""NBA schedule from ESPN's public scoreboard, one day per request, cached.

ESPN rejects date ranges on this endpoint (verified Nov 2026: HTTP 400), so
days are fetched individually. Past days are immutable and cached for a long
time; today and future days use a short TTL because games get postponed.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional

import requests

from .cache import TTLCache
from .categories import NBA_TEAM_CODES, normalize_team
from .config import DEFAULT_PARAMS, ModelParams
from .util import daterange

log = logging.getLogger(__name__)

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
PAST_DAY_TTL = 30 * 24 * 3600
LIVE_DAY_TTL = 15 * 60
FUTURE_DAY_TTL = 6 * 3600


class ScheduleError(RuntimeError):
    pass


def parse_scoreboard_day(payload: dict, exclude_completed: bool = False) -> list[dict[str, Any]]:
    """Return [{"home","away","completed","state"}] for one scoreboard payload."""
    games: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        status = event.get("status", {}).get("type", {})
        completed = bool(status.get("completed", False))
        if exclude_completed and completed:
            continue
        teams = {}
        for competitor in competitions[0].get("competitors", []):
            abbr = normalize_team(competitor.get("team", {}).get("abbreviation", ""))
            if abbr in NBA_TEAM_CODES:
                teams[competitor.get("homeAway", "")] = abbr
        if "home" in teams and "away" in teams:
            games.append({
                "home": teams["home"],
                "away": teams["away"],
                "completed": completed,
                "state": status.get("state", ""),
            })
    return games


class NbaSchedule:
    """Fetches and caches ESPN scoreboard days and answers schedule questions."""

    def __init__(self, cache: Optional[TTLCache] = None, params: ModelParams = DEFAULT_PARAMS,
                 session: Optional[requests.Session] = None, timeout: float = 10.0):
        self.cache = cache or TTLCache()
        self.params = params
        self.session = session or requests.Session()
        self.timeout = timeout
        self.failed_days: set[date] = set()

    # -- fetching -----------------------------------------------------------
    def _ttl_for(self, day: date, today: date) -> float:
        if day < today:
            return PAST_DAY_TTL
        if day == today:
            return LIVE_DAY_TTL
        return FUTURE_DAY_TTL

    def fetch_day(self, day: date, today: date, use_cache: bool = True) -> Optional[dict]:
        key = TTLCache.key_for("espn_scoreboard", day.isoformat())

        def _fetch() -> dict:
            resp = self.session.get(ESPN_SCOREBOARD, params={"dates": day.strftime("%Y%m%d")}, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        try:
            return self.cache.get_or_fetch(key, self._ttl_for(day, today), _fetch, use_cache=use_cache)
        except (requests.RequestException, ValueError) as exc:
            log.warning("ESPN scoreboard fetch failed for %s: %s", day, exc)
            self.failed_days.add(day)
            return None

    def games_on(self, day: date, today: date, exclude_completed: bool = False) -> Optional[list[dict[str, Any]]]:
        payload = self.fetch_day(day, today)
        if payload is None:
            return None
        return parse_scoreboard_day(payload, exclude_completed=exclude_completed)

    # -- aggregate views ----------------------------------------------------
    def games_by_team(self, start: date, end: date, today: date, exclude_completed_today: bool = False) -> dict[str, Any]:
        """Count games per team in [start, end].

        Returns {"counts": {team: n}, "teams_by_day": {iso: [teams]}, "source": espn|espn_partial|fallback,
                 "days": n, "fallback_games": n, "dates": [iso...]}.
        """
        if start > end:
            return {"counts": {}, "teams_by_day": {}, "source": "none", "days": 0, "fallback_games": 0, "dates": []}
        counts: dict[str, int] = defaultdict(int)
        teams_by_day: dict[str, list[str]] = {}
        ok_days = 0
        dates = list(daterange(start, end))
        for day in dates:
            games = self.games_on(day, today, exclude_completed=(exclude_completed_today and day == today))
            if games is None:
                continue
            ok_days += 1
            today_teams: set[str] = set()
            for g in games:
                for team in (g["home"], g["away"]):
                    counts[team] += 1
                    today_teams.add(team)
            teams_by_day[day.isoformat()] = sorted(today_teams)
        days = len(dates)
        per_day = (self.params.fallback_games_per_day_remaining if end >= today
                   else self.params.fallback_games_per_day_lookback)
        fallback_games = max(1, round(days * per_day))
        if ok_days == 0:
            source = "fallback"
        elif ok_days == days:
            source = "espn"
        else:
            source = "espn_partial"
        return {
            "counts": dict(counts),
            "teams_by_day": teams_by_day,
            "source": source,
            "days": days,
            "fallback_games": fallback_games,
            "dates": [d.isoformat() for d in dates],
        }

    def remaining_games(self, week_start: date, week_end: date, today: date) -> dict[str, Any]:
        """Games still to be played this fantasy week, excluding today's finished games."""
        start = max(today, week_start)
        if start > week_end:
            return {"counts": {}, "teams_by_day": {}, "source": "none", "days": 0, "fallback_games": 0, "dates": []}
        return self.games_by_team(start, week_end, today, exclude_completed_today=True)

    def lookback_games(self, days: int, today: date) -> dict[str, Any]:
        """Games played in the `days` days ending yesterday (for per-game normalisation)."""
        end = today - timedelta(days=1)
        return self.games_by_team(end - timedelta(days=days - 1), end, today)
