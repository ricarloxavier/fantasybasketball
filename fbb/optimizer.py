"""Waiver optimizer: rank add/drop scenarios by matchup win-probability gain.

`load_context` gathers everything from Yahoo and ESPN once; `recommend` is pure
and fast (scenarios are `base - drop + add`, not a full re-projection), so the
web app and CLI can re-rank with different options without refetching.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional

from .categories import ALL_CATEGORIES, COUNTING_CATEGORIES, normalize_name_token, parse_punt_categories
from .config import DEFAULT_PARAMS, ModelParams, Settings
from .matchup import (build_category_view, deterministic_score, matchup_win_probability, recommendation_reason,
                      summarize_category_changes, view_probs)
from .projection import (blend_recent, categories_to_totals, combine_totals, format_totals, games_remaining, project_player,
                         project_team, totals_shift)
from .schedule import NbaSchedule
from .util import safe_float, today_in_tz
from .yahoo.client import YahooClient

log = logging.getLogger(__name__)


class WaiverAnalysisError(RuntimeError):
    """A user-facing problem with the request (bad team key, unmatched drop player...)."""


@dataclass
class WaiverOptions:
    free_agent_pool: int = DEFAULT_PARAMS.default_free_agent_pool
    drop_candidates: int = DEFAULT_PARAMS.default_drop_candidates
    max_results: int = DEFAULT_PARAMS.default_max_results
    punt_categories: list[str] = field(default_factory=list)
    forced_drop_players: list[str] = field(default_factory=list)
    never_drop_players: list[str] = field(default_factory=list)
    include_current_score: bool = True
    include_news_context: bool = True
    include_percent_owned_guard: bool = True
    respect_undroppable: bool = True
    ownership_protect_threshold: int = DEFAULT_PARAMS.default_ownership_protect_threshold
    projection_window: str = "recent_2w"   # or "season"


@dataclass
class WaiverContext:
    """Everything the optimizer needs, fetched once."""
    league_key: str
    my_team_key: str
    my_team_name: str
    opp_team_key: str
    opp_team_name: str
    teams: dict[str, dict[str, Any]]
    current_week: int
    week_start: date
    week_end: date
    today: date
    my_players: list[dict[str, Any]]
    opp_players: list[dict[str, Any]]
    free_agents: list[dict[str, Any]]
    stats_source: str
    projection_window_used: str
    recent_windows_info: dict[str, Any]
    games_info: dict[str, Any]
    current_my_totals: dict[str, float]
    current_opp_totals: dict[str, float]
    fetch_signals: Any = None   # callable(player_keys) -> {key: signal}; used for top pickups


def drop_value_score(contribution: Mapping[str, Any], params: ModelParams = DEFAULT_PARAMS,
                     include_percent_owned_guard: bool = True, ownership_protect_threshold: int = 80,
                     respect_undroppable: bool = True) -> float:
    """All-around remaining-week value; lower = easier to drop."""
    score = 0.0
    for cat, weight in params.drop_weights.items():
        score += float(contribution.get(cat, 0.0)) * weight
    fga = float(contribution.get("FGA", 0.0))
    fta = float(contribution.get("FTA", 0.0))
    fg_pct = (contribution.get("FGM", 0.0) / fga) if fga > 0 else params.default_fg_pct
    ft_pct = (contribution.get("FTM", 0.0) / fta) if fta > 0 else params.default_ft_pct
    score += (fg_pct - params.drop_fg_baseline) * fga * params.drop_fg_weight
    score += (ft_pct - params.drop_ft_baseline) * fta * params.drop_ft_weight
    status = (contribution.get("status", "") or "").upper()
    if status in {"O", "INJ", "IR", "IL", "IL+", "NA"}:
        score -= params.drop_penalty_out
    elif status in {"DTD", "GTD"}:
        score -= params.drop_penalty_dtd
    if include_percent_owned_guard:
        threshold = max(0, min(100, int(ownership_protect_threshold)))
        owned = safe_float(contribution.get("percent_owned", 0.0))
        if owned >= threshold:
            score += params.ownership_guard_base + (owned - threshold) * params.ownership_guard_per_point
        elif owned >= max(threshold - params.ownership_guard_near_band, 0):
            score += params.ownership_guard_near_bonus
    if respect_undroppable and contribution.get("is_undroppable", False):
        score += params.undroppable_bonus
    return score


def signals_from_details(details: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Reduce Yahoo player detail rows to the status/news fields the models use."""
    out: dict[str, dict[str, Any]] = {}
    for key, row in details.items():
        note = row.get("injury_note", "") or ""
        out[key] = {
            "status": (row.get("status") or "").upper(),
            "status_full": row.get("status_full", "") or "",
            "news": note,
            "return_hint": "",
            "is_undroppable": bool(row.get("is_undroppable", False)),
        }
    return out


def load_context(client: YahooClient, schedule: NbaSchedule, options: WaiverOptions, team_key: str = "",
                 params: ModelParams = DEFAULT_PARAMS, timezone: str = "America/New_York") -> WaiverContext:
    """Fetch rosters, stats, free agents, schedule and banked scores for the current matchup."""
    teams = client.teams()
    my_team_key = team_key or client.team_key
    if my_team_key not in teams:
        raise WaiverAnalysisError(f"Team {my_team_key} is not in league {client.league_key}.")
    week = client.current_week()
    matchup = client.matchup(my_team_key, week)
    opp_team_key = matchup["opponent_team_key"]
    week_start = date.fromisoformat(matchup["week_start"])
    week_end = date.fromisoformat(matchup["week_end"])
    today = today_in_tz(timezone)

    my_roster = client.active_roster(client.roster(my_team_key, day=today))
    opp_roster = client.active_roster(client.roster(opp_team_key, day=today)) if opp_team_key else []
    roster_keys = [p["player_key"] for p in my_roster + opp_roster]

    stats_source = "average_season"
    window_used = "season"
    recent_info: dict[str, Any] = {}
    roster_stats: dict[str, dict[str, Any]] = {}
    free_agents: list[dict[str, Any]] = []

    def merge_meta(target: dict[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
        merged = dict(source)
        merged["stats"] = target
        return merged

    if options.projection_window == "recent_2w":
        week_rows = client.player_stats(roster_keys, "lastweek")
        month_rows = client.player_stats(roster_keys, "lastmonth")
        fa_month = {r["player_key"]: r for r in client.free_agents(options.free_agent_pool, "lastmonth")}
        fa_week = client.player_stats(list(fa_month), "lastweek")
        lookback_week = schedule.lookback_games(7, today)
        lookback_month = schedule.lookback_games(30, today)
        wk_counts, mo_counts = lookback_week["counts"], lookback_month["counts"]
        wk_fb, mo_fb = lookback_week["fallback_games"], lookback_month["fallback_games"]
        recent_info = {
            "week_source": lookback_week["source"], "week_days": lookback_week["days"], "week_fallback_games": wk_fb,
            "month_source": lookback_month["source"], "month_days": lookback_month["days"], "month_fallback_games": mo_fb,
        }
        for key in roster_keys:
            base = month_rows.get(key) or week_rows.get(key)
            if base:
                roster_stats[key] = merge_meta(blend_recent(week_rows.get(key), month_rows.get(key), wk_counts, mo_counts, wk_fb, mo_fb, params), base)
        for key in sorted(set(fa_month) | set(fa_week)):
            base = fa_month.get(key) or fa_week.get(key)
            if not base:
                continue
            merged = merge_meta(blend_recent(fa_week.get(key), fa_month.get(key), wk_counts, mo_counts, wk_fb, mo_fb, params), base)
            if sum(abs(merged["stats"].get(c, 0.0)) for c in COUNTING_CATEGORIES) > 0:
                free_agents.append(merged)
        stats_source = "per_game_recent_2w"
        window_used = "recent_2w"
    else:
        roster_stats = client.player_stats(roster_keys, "average_season")
        free_agents = [r for r in client.free_agents(options.free_agent_pool, "average_season")
                       if sum(abs(r["stats"].get(c, 0.0)) for c in COUNTING_CATEGORIES) > 0]

    my_keys = [p["player_key"] for p in my_roster]
    owned = client.percent_owned(my_keys)
    details = client.player_details(my_keys)
    signals = signals_from_details(details)

    def enrich(raw_roster: list[dict[str, Any]], with_signals: bool) -> list[dict[str, Any]]:
        out = []
        for row in raw_roster:
            key = row["player_key"]
            pdata = roster_stats.get(key)
            if not pdata:
                log.info("No stats for %s (%s); skipped", row.get("name"), key)
                continue
            merged = dict(pdata)
            sig = signals.get(key, {}) if with_signals else {}
            merged["status"] = (row.get("status") or sig.get("status") or merged.get("status") or "").upper()
            merged["player_id"] = int(safe_float(row.get("player_id", 0)))
            merged["selected_position"] = row.get("selected_position", "")
            if with_signals:
                merged["percent_owned"] = owned.get(key, 0.0)
            merged["status_full"] = sig.get("status_full", "") or row.get("status_full", "")
            merged["news"] = sig.get("news", "") or row.get("injury_note", "")
            merged["return_hint"] = sig.get("return_hint", "")
            merged["is_undroppable"] = bool(sig.get("is_undroppable", False))
            out.append(merged)
        return out

    games_info = schedule.remaining_games(week_start, week_end, today)
    current_my = categories_to_totals(client.team_week_stats(my_team_key, week), params)
    current_opp = categories_to_totals(client.team_week_stats(opp_team_key, week), params) if opp_team_key else categories_to_totals({}, params)

    return WaiverContext(
        league_key=client.league_key, my_team_key=my_team_key, my_team_name=teams[my_team_key]["name"],
        opp_team_key=opp_team_key, opp_team_name=teams.get(opp_team_key, {}).get("name", "Opponent"), teams=teams,
        current_week=week, week_start=week_start, week_end=week_end, today=today,
        my_players=enrich(my_roster, True), opp_players=enrich(opp_roster, False), free_agents=free_agents,
        stats_source=stats_source, projection_window_used=window_used, recent_windows_info=recent_info,
        games_info=games_info, current_my_totals=current_my, current_opp_totals=current_opp,
        fetch_signals=lambda keys: signals_from_details(client.player_details(keys)),
    )


def _match_requested(requested: list[str], contribs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_key = {c["player_key"]: c for c in contribs}
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in contribs:
        by_name[normalize_name_token(c.get("name", ""))].append(c)
    found: list[dict[str, Any]] = []
    unmatched: list[str] = []
    seen: set[str] = set()
    for token in requested:
        rows = [by_key[token]] if token in by_key else by_name.get(normalize_name_token(token), [])
        if not rows:
            unmatched.append(token)
        for row in rows:
            if row["player_key"] not in seen:
                found.append(row)
                seen.add(row["player_key"])
    return found, unmatched


def recommend(ctx: WaiverContext, options: WaiverOptions, params: ModelParams = DEFAULT_PARAMS) -> dict[str, Any]:
    """Rank add/drop scenarios for the context. Pure: no network."""
    punt = parse_punt_categories(options.punt_categories)
    counts = ctx.games_info["counts"]
    fallback = ctx.games_info["fallback_games"]
    remaining_days = ctx.games_info.get("days", 0)
    week_days = (ctx.week_end - ctx.week_start).days + 1
    remaining_share = remaining_days / week_days if week_days > 0 else 0.0

    remaining_my, my_contribs = project_team(ctx.my_players, ctx.stats_source, counts, fallback, params, options.include_news_context)
    remaining_opp, _ = project_team(ctx.opp_players, ctx.stats_source, counts, fallback, params, options.include_news_context)
    if options.include_current_score:
        base_my = combine_totals(ctx.current_my_totals, remaining_my)
        base_opp = combine_totals(ctx.current_opp_totals, remaining_opp)
        basis = "current_score_plus_remaining_games"
    else:
        base_my, base_opp, basis = dict(remaining_my), dict(remaining_opp), "remaining_games_only"

    base_view = build_category_view(base_my, base_opp, punt, params, remaining_share)
    current_view = build_category_view(ctx.current_my_totals, ctx.current_opp_totals, punt, params, remaining_share)
    base_probs = view_probs(base_view)
    base_wp = matchup_win_probability(base_probs, punt)
    weaknesses = sorted([c for c in ALL_CATEGORIES if c not in punt and base_view[c]["prob_you_win"] < 0.5],
                        key=lambda c: base_view[c]["prob_you_win"])

    score_kw = dict(params=params, include_percent_owned_guard=options.include_percent_owned_guard,
                    ownership_protect_threshold=options.ownership_protect_threshold, respect_undroppable=options.respect_undroppable)
    ranked = sorted(my_contribs, key=lambda c: drop_value_score(c, **score_kw))
    requested_drop = [p.strip() for p in options.forced_drop_players if p and p.strip()]
    requested_never = [p.strip() for p in options.never_drop_players if p and p.strip()]
    if requested_drop:
        drop_mode = "manual"
        drop_pool, unmatched = _match_requested(requested_drop, my_contribs)
        if unmatched:
            raise WaiverAnalysisError("Could not match selected drop player(s): " + ", ".join(unmatched))
        if not drop_pool:
            raise WaiverAnalysisError("No valid selected drop players found on current roster.")
    else:
        drop_mode = "auto"
        if requested_never:
            protected, _ = _match_requested(requested_never, my_contribs)
            protected_keys = {c["player_key"] for c in protected}
            ranked = [c for c in ranked if c["player_key"] not in protected_keys]
            if not ranked:
                raise WaiverAnalysisError("No auto-drop candidates left after applying never-drop list.")
        drop_pool = ranked[:max(1, min(options.drop_candidates, len(ranked)))]

    roster_keys = {p["player_key"] for p in ctx.my_players}
    add_contribs = [project_player(fa, ctx.stats_source, counts, fallback, params, options.include_news_context)
                    for fa in ctx.free_agents if fa["player_key"] not in roster_keys]
    scenarios: list[dict[str, Any]] = []
    for drop in drop_pool:
        for add in add_contribs:
            totals = totals_shift(base_my, remove=drop, add=add)
            view = build_category_view(totals, base_opp, punt, params, remaining_share)
            wp = matchup_win_probability(view_probs(view), punt)
            deltas, flips = summarize_category_changes(base_view, view)
            scenarios.append({
                "pickup_player": add["name"], "pickup_player_id": add["player_id"], "pickup_player_key": add["player_key"],
                "pickup_team": add["nba_team"], "pickup_position": add["position"], "pickup_status": add["status"],
                "pickup_games_remaining": add["games_remaining"],
                "drop_player": drop["name"], "drop_player_key": drop["player_key"], "drop_team": drop["nba_team"],
                "drop_position": drop["position"], "drop_status": drop["status"], "drop_games_remaining": drop["games_remaining"],
                "delta_win_probability": wp - base_wp, "new_win_probability": wp,
                "category_deltas": deltas[:5], "flipped_categories": flips,
                "reason": recommendation_reason(deltas, flips), "new_totals": format_totals(totals),
            })
    scenarios.sort(key=lambda r: (r["delta_win_probability"], len(r["flipped_categories"]), r["new_win_probability"]), reverse=True)
    top = scenarios[:options.max_results]
    for i, row in enumerate(top, 1):
        row["rank"] = i
    if top and ctx.fetch_signals:
        try:
            sig = ctx.fetch_signals([r["pickup_player_key"] for r in top])
        except Exception as exc:  # signals are decoration; the ranking stands without them
            log.warning("Could not fetch pickup signals: %s", exc)
            sig = {}
        for row in top:
            s = sig.get(row["pickup_player_key"], {})
            row["pickup_news"] = s.get("news", "")
            row["pickup_status_full"] = s.get("status_full", "")
            row["pickup_return_hint"] = s.get("return_hint", "")

    def player_payload(c: Mapping[str, Any], projected: bool) -> dict[str, Any]:
        out = {
            "name": c.get("name", ""), "player_key": c.get("player_key", ""), "player_id": int(safe_float(c.get("player_id", 0))),
            "team": c.get("nba_team", ""), "position": c.get("position", ""), "status": c.get("status", ""),
            "status_full": c.get("status_full", ""), "percent_owned": round(safe_float(c.get("percent_owned", 0.0)), 1),
            "is_undroppable": bool(c.get("is_undroppable", False)), "news": c.get("news", ""), "return_hint": c.get("return_hint", ""),
            "games_remaining": round(float(c.get("games_remaining", 0.0)), 2),
            "drop_score": round(drop_value_score(c, **score_kw), 2),
        }
        if projected:
            out["projected"] = {cat: round(float(c.get(cat, 0.0)), 2) for cat in ("PTS", "REB", "AST", "ST", "BLK", "3PTM", "TO")}
        return out

    contrib_by_key = {c["player_key"]: c for c in my_contribs}
    schedule_rows = []
    teams_by_day = ctx.games_info.get("teams_by_day", {})
    for day in ctx.games_info.get("dates", []):
        today_teams = set(teams_by_day.get(day, []))
        mine = sorted(_label(p) for p in ctx.my_players if _team(p) in today_teams)
        theirs = sorted(_label(p) for p in ctx.opp_players if _team(p) in today_teams)
        schedule_rows.append({"date": day, "my_players": mine, "opp_players": theirs, "my_count": len(mine), "opp_count": len(theirs)})

    def view_payload(view, with_prob: bool):
        return {cat: {k: (round(v, 4) if isinstance(v, float) and k != "prob_you_win" else v)
                      for k, v in view[cat].items() if with_prob or k != "prob_you_win"} for cat in ALL_CATEGORIES}

    return {"success": True, "data": {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "league_key": ctx.league_key, "my_team_key": ctx.my_team_key, "my_team_name": ctx.my_team_name,
        "opponent_team_key": ctx.opp_team_key, "opponent_team_name": ctx.opp_team_name,
        "available_teams": team_options(ctx.teams),
        "current_week": ctx.current_week, "week_start": ctx.week_start.isoformat(), "week_end": ctx.week_end.isoformat(),
        "today": ctx.today.isoformat(), "games_source": ctx.games_info["source"], "days_remaining": remaining_days,
        "games_remaining_by_team": counts, "stats_source": ctx.stats_source,
        "projection_window_requested": options.projection_window, "projection_window_used": ctx.projection_window_used,
        "recent_windows_info": ctx.recent_windows_info, "projection_basis": basis,
        "model_notes": [] if params.remaining_games_scaling == 0 else ["Category odds tighten as the week runs out (remaining-games scaling)."],
        "settings": {
            "free_agent_pool": options.free_agent_pool, "drop_candidates": options.drop_candidates, "max_results": options.max_results,
            "projection_window_requested": options.projection_window, "projection_window_used": ctx.projection_window_used,
            "selected_team_key": ctx.my_team_key, "punt_categories": punt, "drop_mode": drop_mode,
            "requested_drop_players": requested_drop, "requested_never_drop_players": requested_never,
            "include_current_score": options.include_current_score, "include_news_context": options.include_news_context,
            "include_percent_owned_guard": options.include_percent_owned_guard, "respect_undroppable": options.respect_undroppable,
            "ownership_protect_threshold": options.ownership_protect_threshold,
        },
        "my_roster": [player_payload({**p, **contrib_by_key.get(p["player_key"], {})}, False)
                      for p in sorted(ctx.my_players, key=lambda r: r.get("name", ""))],
        "current_score": {
            "my_totals": format_totals(ctx.current_my_totals), "opp_totals": format_totals(ctx.current_opp_totals),
            "categories": view_payload(current_view, False), "deterministic_score": deterministic_score(current_view, punt),
        },
        "remaining_schedule": {"dates": ctx.games_info.get("dates", []), "rows": schedule_rows, "source": ctx.games_info["source"]},
        "baseline": {
            "win_probability": base_wp,
            "expected_categories_won": sum(base_probs[c] for c in ALL_CATEGORIES if c not in punt),
            "deterministic_score": deterministic_score(base_view, punt, by_probability=True),
            "my_totals": format_totals(base_my), "opp_totals": format_totals(base_opp),
            "categories": view_payload(base_view, True), "my_weaknesses": weaknesses, "opponent_strengths": list(weaknesses),
        },
        "drop_candidates": [player_payload(c, True) for c in drop_pool],
        "recommendations": top,
    }}


def _team(p: Mapping[str, Any]) -> str:
    from .categories import normalize_team
    return normalize_team(p.get("nba_team", ""))


def _label(p: Mapping[str, Any]) -> str:
    status = (p.get("status", "") or "").upper()
    return f"{p.get('name', '')} ({status})" if status in {"DTD", "GTD", "O", "INJ"} else p.get("name", "")


def team_options(teams: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{"team_key": k, "team_name": v.get("name", k)} for k, v in sorted(teams.items(), key=lambda kv: (kv[1].get("name", ""), kv[0]))]


def get_waiver_recommendations(settings: Settings, options: Optional[WaiverOptions] = None, team_key: str = "",
                               client: Optional[YahooClient] = None, schedule: Optional[NbaSchedule] = None,
                               params: ModelParams = DEFAULT_PARAMS) -> dict[str, Any]:
    """Convenience entry point used by the web app and CLI."""
    from .cache import TTLCache
    options = options or WaiverOptions()
    client = client or YahooClient.from_settings(settings, params)
    schedule = schedule or NbaSchedule(cache=TTLCache(settings.cache_dir, "espn"), params=params)
    ctx = load_context(client, schedule, options, team_key=team_key, params=params, timezone=settings.timezone)
    return recommend(ctx, options, params)


def get_waiver_team_options(settings: Settings, preferred_team_key: str = "", client: Optional[YahooClient] = None) -> dict[str, Any]:
    client = client or YahooClient.from_settings(settings)
    teams = client.teams()
    selected = preferred_team_key if preferred_team_key in teams else client.team_key
    return {"success": True, "data": {"league_key": client.league_key, "selected_team_key": selected, "teams": team_options(teams)}}
