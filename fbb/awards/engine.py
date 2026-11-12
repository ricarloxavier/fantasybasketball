"""Putilla awards: who rostered the most frequently-dropped players, and how it went.

A "putilla" is a player dropped at least twice in the league. Team awards count
the weeks each team spent starting putillas; player awards rank the stints.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Optional

from ..cache import TTLCache
from ..config import Settings
from ..util import now_in_tz
from ..yahoo.client import YahooClient
from . import data as D

log = logging.getLogger(__name__)

MIN_DROPS = 2
ESCONDIDA_MIN_WEEKS = 5
MVP_MIN_WEEKS = 10
AWARDS_TTL_SECONDS = 6 * 3600


class AwardsError(RuntimeError):
    pass


def _team_award(ranked: list[tuple[str, int]], players_by_team: Mapping[str, set[str]], metric: str, runners: int = 3) -> Optional[dict[str, Any]]:
    if not ranked:
        return None

    def entry(team: str, value: int) -> dict[str, Any]:
        return {"team_name": team, "player_name": "Team Total", metric: value, "player_list": sorted(players_by_team.get(team, set()))}

    return {"winner": entry(*ranked[0]), "runners_up": [entry(t, v) for t, v in ranked[1:1 + runners]]}


def _player_award(ranked: list[dict[str, Any]], metric: str) -> Optional[dict[str, Any]]:
    if not ranked:
        return None

    def entry(s: dict[str, Any]) -> dict[str, Any]:
        return {"team_name": s["team_name"], "player_name": s["player_name"], metric: s[metric], "games_started": s["weeks"]}

    return {"winner": entry(ranked[0]), "runners_up": [entry(s) for s in ranked[1:4]]}


def calculate_awards(stint_values: list[dict[str, Any]], all_stints: Mapping[str, list[dict[str, Any]]],
                     drop_counts: Mapping[str, int], current_week: int) -> dict[str, Any]:
    """Pure award computation from valued putilla stints (see `compute_awards` for the fetch)."""
    weeks_by_team: dict[str, int] = {}
    players_by_team: dict[str, set[str]] = {}
    for s in stint_values:
        team = s["team_name"]
        weeks_by_team[team] = weeks_by_team.get(team, 0) + s["weeks"]
        players_by_team.setdefault(team, set()).add(s["player_name"])
    by_weeks = sorted(weeks_by_team.items(), key=lambda kv: kv[1], reverse=True)
    unique_counts = {team: len(p) for team, p in players_by_team.items()}
    by_unique = sorted(unique_counts.items(), key=lambda kv: kv[1], reverse=True)
    by_faithful = sorted(unique_counts.items(), key=lambda kv: kv[1])

    housewife: dict[str, set[str]] = {}
    for player, stints in all_stints.items():
        if drop_counts.get(player, 0) == 0:
            for stint in stints:
                housewife.setdefault(stint["team_name"], set()).add(player)
    by_housewife = sorted(((t, len(p)) for t, p in housewife.items()), key=lambda kv: kv[1], reverse=True)

    team_awards = {
        "putilla_suprema": _team_award(by_weeks, players_by_team, "games_started"),
        "putilla_de_plata": _team_award(by_weeks[1:2], players_by_team, "games_started", runners=0) if len(by_weeks) > 1 else None,
        "putilla_de_bronce": _team_award(by_weeks[2:3], players_by_team, "games_started", runners=0) if len(by_weeks) > 2 else None,
        "putilla_mas_activa": _team_award(by_unique, players_by_team, "unique_players"),
        "putilla_fiel": _team_award(by_faithful, players_by_team, "unique_players"),
        "el_putilla_housewife": _team_award(by_housewife, housewife, "unique_players"),
    }

    eligible = [s for s in stint_values if s["weeks"] >= ESCONDIDA_MIN_WEEKS]
    best_per_game = sorted(eligible, key=lambda s: s["value_per_game"], reverse=True)
    mvp_ranked = sorted((s for s in stint_values if s["weeks"] >= MVP_MIN_WEEKS), key=lambda s: s["total_value"], reverse=True)
    mvp_name = mvp_ranked[0]["player_name"] if mvp_ranked else None
    worst_per_game = sorted((s for s in eligible if s["player_name"] != mvp_name), key=lambda s: s["value_per_game"])
    player_awards = {
        "la_putilla_escondida": _player_award(best_per_game, "value_per_game"),
        "putilla_MVP": _player_award(mvp_ranked, "total_value"),
        "putilla_basura": _player_award(worst_per_game, "value_per_game"),
    }
    return {"team_awards": team_awards, "player_awards": player_awards, "total_stints": len(stint_values), "current_week": current_week}


def compute_awards(client: YahooClient, settings: Settings, cache: Optional[TTLCache] = None, use_cache: bool = True) -> dict[str, Any]:
    """Fetch transactions and stats, value the putilla stints and return the awards payload."""
    cache = cache or TTLCache(settings.cache_dir, "awards")
    key = TTLCache.key_for("awards", client.league_key)

    def build() -> dict[str, Any]:
        meta = client.league_meta()
        current_week = int(meta["current_week"])
        max_week = int(meta.get("end_week") or 23)
        if not meta.get("start_date"):
            raise AwardsError("League metadata has no start_date; cannot map transactions to weeks.")
        origin = D.season_week_origin(meta["start_date"])
        transactions = client.transactions("add,drop")
        all_stints, drop_counts = D.build_roster_timeline(transactions)
        putilla_stints = {p: s for p, s in all_stints.items() if drop_counts.get(p, 0) >= MIN_DROPS}
        keys = sorted({s["player_key"] for rows in putilla_stints.values() for s in rows if s.get("player_key")})
        log.info("Awards: %d transactions, %d putillas, fetching stats for %d players", len(transactions), len(putilla_stints), len(keys))
        season_stats = D.fetch_season_stats(client, keys)
        stint_values = D.analyze_stints(putilla_stints, season_stats, current_week, origin, max_week, drop_counts, settings.timezone)
        result = calculate_awards(stint_values, all_stints, drop_counts, current_week)
        result["generated_at"] = now_in_tz(settings.timezone).strftime("%B %d, %Y at %I:%M %p")
        result["league_key"] = client.league_key
        result["league_name"] = meta.get("name", "")
        return result

    return cache.get_or_fetch(key, AWARDS_TTL_SECONDS, build, use_cache=use_cache)
