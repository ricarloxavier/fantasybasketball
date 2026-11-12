"""Roster decisions from Hashtag Basketball data: drop X for Y, judged on three horizons.

- this week:   change in matchup win probability from the planner's projected end-of-week totals
- next week:   per-game value (z-score) times games next week, minus the same for the drop
- rest of season: per-game z-score difference, scaled by availability

Everything is expressed in "win-probability points" so the horizons can be added.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Optional

from ..categories import ALL_CATEGORIES, COUNTING_CATEGORIES, PERCENT_CATEGORIES, name_key, normalize_team
from ..config import DEFAULT_PARAMS, ModelParams
from ..matchup import build_category_view, calibrate_scales, matchup_win_probability, summarize_category_changes, view_probs
from ..projection import estimate_volume, finalize_percentages
from ..schedule import NbaSchedule
from ..util import clamp

log = logging.getLogger(__name__)

DAY_INDEX = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


@dataclass
class DecisionWeights:
    this_week: float = 1.0          # per win-probability point this week
    next_week: float = 0.7          # points per value-unit per game next week
    ros: float = 5.0                # points per value-unit of rest-of-season per-game production
    closeness_boost: float = 2.0    # this-week gain counts up to (1+boost)x when the matchup is a coin flip
    hold_threshold: float = 1.0     # best score below this = hold
    streaming_ros_tolerance: float = 0.15  # value-units of ROS production you accept losing for a this-week gain
    punt: list[str] = field(default_factory=list)
    # availability multipliers by Hashtag injury flag
    injury_now: Mapping[str, float] = field(default_factory=lambda: {"out": 0.0, "injured": 0.0, "doubt": 0.75})
    injury_ros: Mapping[str, float] = field(default_factory=lambda: {"out": 0.7, "injured": 0.6, "doubt": 0.9})
    opportunity_clamp: tuple[float, float] = (0.7, 1.3)


# ---------------------------------------------------------------------------
def per_game_value_table(pool: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Means/stds of the 9 categories over a player pool, for z-scores. Percentages are volume-weighted impact."""
    cols: dict[str, list[float]] = {cat: [] for cat in ALL_CATEGORIES}
    for p in pool:
        s = p.get("stats", {})
        if not s:
            continue
        vol = estimate_volume(s)
        for cat in COUNTING_CATEGORIES:
            cols[cat].append(float(s.get(cat, 0.0)))
        cols["FG%"].append(vol["FGM"])
        cols["FT%"].append(vol["FTM"])
    fga = [estimate_volume(p["stats"])["FGA"] for p in pool if p.get("stats")]
    fta = [estimate_volume(p["stats"])["FTA"] for p in pool if p.get("stats")]
    league_fg = sum(cols["FG%"]) / sum(fga) if sum(fga) else 0.46
    league_ft = sum(cols["FT%"]) / sum(fta) if sum(fta) else 0.76
    table: dict[str, Any] = {"league_fg": league_fg, "league_ft": league_ft, "n": len(cols["PTS"])}
    for cat in COUNTING_CATEGORIES:
        vals = cols[cat]
        table[cat] = (statistics.fmean(vals) if vals else 0.0, statistics.pstdev(vals) if len(vals) > 1 else 1.0)
    fg_imp = [(estimate_volume(p["stats"])["FGM"] - league_fg * estimate_volume(p["stats"])["FGA"]) for p in pool if p.get("stats")]
    ft_imp = [(estimate_volume(p["stats"])["FTM"] - league_ft * estimate_volume(p["stats"])["FTA"]) for p in pool if p.get("stats")]
    table["FG%"] = (statistics.fmean(fg_imp) if fg_imp else 0.0, statistics.pstdev(fg_imp) if len(fg_imp) > 1 else 1.0)
    table["FT%"] = (statistics.fmean(ft_imp) if ft_imp else 0.0, statistics.pstdev(ft_imp) if len(ft_imp) > 1 else 1.0)
    return table


def player_z(stats: Mapping[str, float], table: Mapping[str, Any], punt: list[str] = ()) -> tuple[float, dict[str, float]]:
    """Per-game production in category-spread units (punted categories excluded) and the per-category breakdown.

    Counting stats are measured above zero (an empty slot produces nothing), each divided by the pool's standard
    deviation so a block counts like a block and a point like a point. Percentages are volume-weighted impact
    versus the pool's shooting, which can be negative. Turnovers subtract.
    """
    vol = estimate_volume(stats)
    per_cat: dict[str, float] = {}
    for cat in ALL_CATEGORIES:
        mean, std = table[cat]
        std = std or 1.0
        if cat == "FG%":
            raw = vol["FGM"] - table["league_fg"] * vol["FGA"]
        elif cat == "FT%":
            raw = vol["FTM"] - table["league_ft"] * vol["FTA"]
        else:
            raw = float(stats.get(cat, 0.0))
        z = raw / std
        per_cat[cat] = -z if cat == "TO" else z
    active = [cat for cat in ALL_CATEGORIES if cat not in punt]
    total = sum(per_cat[cat] for cat in active) / max(len(active), 1)
    return total, per_cat


def contribution(stats: Mapping[str, float], games: float) -> dict[str, float]:
    vol = estimate_volume(stats)
    out = {cat: float(stats.get(cat, 0.0)) * games for cat in COUNTING_CATEGORIES}
    out.update({k: v * games for k, v in vol.items()})
    return out


def team_totals_from_planner(after: Mapping[str, float], roster: list[Mapping[str, Any]]) -> dict[str, float]:
    """Planner AFTER totals with FGM/FGA/FTM/FTA reconstructed from the roster so swaps can move the percentages."""
    totals = {cat: float(after.get(cat, 0.0)) for cat in COUNTING_CATEGORIES}
    fga = fta = 0.0
    for p in roster:
        vol = estimate_volume(p["stats"])
        g = p.get("games_left", 0)
        fga += vol["FGA"] * g
        fta += vol["FTA"] * g
    # Scale the volume so that the planner's percentages are exactly reproduced.
    fg, ft = float(after.get("FG%", 0.0)), float(after.get("FT%", 0.0))
    week_games = max(sum(p.get("games_left", 0) for p in roster), 1)
    total_fga = max(fga, 1.0) * (1 + 7.0 / max(week_games, 1))  # banked part approximated as a full week
    total_fta = max(fta, 1.0) * (1 + 7.0 / max(week_games, 1))
    totals.update({"FGA": total_fga, "FGM": fg * total_fga, "FTA": total_fta, "FTM": ft * total_fta})
    return finalize_percentages(totals)


def addable_days(candidate_days: list[str], days: list[Mapping[str, Any]], drop_days: set[str]) -> list[str]:
    """Days the pickup can be started: a day fits if an active spot is free or the dropped player used one."""
    out = []
    caps = {d["label"]: d for d in days}
    for day in candidate_days:
        cap = caps.get(day)
        if cap is None:
            continue
        used, spots = cap.get("used"), cap.get("spots")
        if used is None or spots is None or used < spots or day in drop_days:
            out.append(day)
    return out


# ---------------------------------------------------------------------------
@dataclass
class DecisionContext:
    planner: dict[str, Any]                       # ROS projections, with recommendations
    free_agents: list[dict[str, Any]]             # waiver wire ROS rows
    today: date
    week_start: date
    next_week_games: Mapping[str, int]            # NBA team -> games next week (ESPN)
    remaining_teams_by_day: Mapping[str, list[str]]  # iso date -> teams playing (ESPN, from today)
    recent_mpg: Mapping[str, float] = field(default_factory=dict)   # name_key -> STREAM/BLEND MPG (opportunity)
    yahoo: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # name_key -> {status, is_undroppable, is_free_agent}
    hashtag_probs: Mapping[str, float] = field(default_factory=dict)


def _find_roster(planner: dict[str, Any], query: str) -> dict[str, Any]:
    key = name_key(query)
    exact = [p for p in planner["roster"] if p["name_key"] == key]
    if len(exact) == 1:
        return exact[0]
    partial = [p for p in planner["roster"] if key in p["name_key"]]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ValueError(f"No roster player matches {query!r}. Roster: " + ", ".join(p["name"] for p in planner["roster"]))
    raise ValueError(f"{query!r} is ambiguous: " + ", ".join(p["name"] for p in partial))


def _weekday_labels_from(today: date, week_start: date) -> list[str]:
    labels = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        if d >= today:
            labels.append(list(DAY_INDEX)[i])
    return labels


def evaluate_drop(ctx: DecisionContext, drop_query: str, weights: DecisionWeights = DecisionWeights(), top: int = 8,
                  params: ModelParams = DEFAULT_PARAMS, candidates: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    planner = ctx.planner
    drop = _find_roster(planner, drop_query)
    my_team, opp_team = planner["team_order"][0], planner["team_order"][1]
    punt = list(weights.punt)
    days_left = [d["label"] for d in planner["days"]]
    week_days = 7
    remaining_share = len(days_left) / week_days

    base_my = team_totals_from_planner(planner["after"][my_team], planner["roster"])
    base_opp = {cat: float(planner["after"][opp_team].get(cat, 0.0)) for cat in ALL_CATEGORIES}
    # Calibrate the category odds to Hashtag's own probability row, so deltas are measured on their scale.
    scales = calibrate_scales(base_my, base_opp, ctx.hashtag_probs, params) if ctx.hashtag_probs else None
    base_view = build_category_view(base_my, base_opp, punt, params, remaining_share, scales)
    base_probs = view_probs(base_view)
    base_wp = matchup_win_probability(base_probs, punt)
    hashtag_wp = matchup_win_probability(ctx.hashtag_probs, punt) if ctx.hashtag_probs else None
    closeness = 1.0 + weights.closeness_boost * (0.5 - abs(base_wp - 0.5)) * 2.0 * 0.5  # 1 at 0/100%, 1+boost at 50%

    # Value scale from the free-agent pool (replacement level); roster players are scored on the same scale.
    table = per_game_value_table([p for p in ctx.free_agents if p.get("stats")] or [p for p in planner["roster"] if p.get("stats")])

    drop_days = {g["day"] for g in drop["games"] if g["counted"]}
    drop_contrib = contribution(drop["stats"], drop["games_left"])
    drop_z, drop_z_cats = player_z(drop["stats"], table, punt)
    drop_injury = drop.get("injury")
    drop_next_games = ctx.next_week_games.get(drop["nba_team"], 0) * weights.injury_now.get(drop_injury, 1.0) if drop_injury else ctx.next_week_games.get(drop["nba_team"], 0)
    drop_ros_avail = weights.injury_ros.get(drop_injury, 1.0) if drop_injury else 1.0
    drop_opp = _opportunity(drop, ctx.recent_mpg, weights)

    recs_by_key = {r["name_key"]: r for r in planner.get("recommendations", [])}
    roster_keys = {p["name_key"] for p in planner["roster"]}
    rows: list[dict[str, Any]] = []
    for cand in (candidates if candidates is not None else ctx.free_agents):
        if not cand.get("stats") or cand["name_key"] in roster_keys or cand["name_key"] == drop["name_key"]:
            continue
        rec = recs_by_key.get(cand["name_key"])
        if rec and rec.get("games_this_week"):
            week_days_c = rec["games_this_week"]
        else:
            team = normalize_team(cand.get("nba_team", ""))
            week_days_c = []
            for iso, teams in ctx.remaining_teams_by_day.items():
                if team in teams:
                    week_days_c.append(list(DAY_INDEX)[date.fromisoformat(iso).weekday()])
        playable = addable_days([d for d in week_days_c if d in days_left], planner["days"], drop_days)
        avail_now = weights.injury_now.get(cand.get("injury"), 1.0) if cand.get("injury") else 1.0
        games_now = len(playable) * avail_now
        add_contrib = contribution(cand["stats"], games_now)

        totals = {cat: max(base_my[cat] - drop_contrib[cat] + add_contrib[cat], 0.0) for cat in base_my if cat not in PERCENT_CATEGORIES}
        totals = finalize_percentages(totals)
        view = build_category_view(totals, base_opp, punt, params, remaining_share, scales)
        wp = matchup_win_probability(view_probs(view), punt)
        deltas, flips = summarize_category_changes(base_view, view)
        d_week = (wp - base_wp) * 100.0

        cand_z, cand_z_cats = player_z(cand["stats"], table, punt)
        cand_opp = _opportunity(cand, ctx.recent_mpg, weights)
        cand_next_games = ctx.next_week_games.get(normalize_team(cand.get("nba_team", "")), 0)
        if rec and rec.get("games_next_week"):
            cand_next_games = len(rec["games_next_week"])
        cand_next_games *= weights.injury_now.get(cand.get("injury"), 1.0) if cand.get("injury") else 1.0
        d_next_z = cand_z * cand_opp * cand_next_games - drop_z * drop_opp * drop_next_games
        d_next = d_next_z * weights.next_week
        cand_ros_avail = weights.injury_ros.get(cand.get("injury"), 1.0) if cand.get("injury") else 1.0
        d_ros_z = cand_z * cand_ros_avail * cand_opp - drop_z * drop_ros_avail * drop_opp
        d_ros = d_ros_z * weights.ros

        y = ctx.yahoo.get(cand["name_key"], {})
        notes = []
        if cand.get("injury"):
            notes.append(f"injury: {cand['injury']}")
        if cand.get("note"):
            notes.append(cand["note"])
        if y.get("status"):
            notes.append(f"Yahoo status {y['status']}")
        if y and y.get("is_free_agent") is False:
            notes.append("NOT a free agent on Yahoo")
        score = weights.this_week * d_week * closeness + d_next + d_ros
        rows.append({
            "add": cand["name"], "add_team": cand.get("nba_team", ""), "add_positions": cand.get("positions", []), "add_rank": cand.get("rank"),
            "games_this_week": round(games_now, 2), "playable_days": playable, "games_next_week": round(cand_next_games, 2),
            "delta_week_pts": round(d_week, 2), "new_win_probability": wp, "flips": flips,
            "category_deltas": [{"category": r["category"], "delta_prob": round(r["delta_prob"], 3)} for r in deltas[:3]],
            "z_per_game": round(cand_z, 2), "opportunity": round(cand_opp, 2), "delta_next_week_pts": round(d_next, 2),
            "delta_ros_pts": round(d_ros, 2), "delta_ros_z": round(d_ros_z, 2), "score": round(score, 2), "notes": notes,
            "mpg": cand.get("mpg"), "total_z_site": cand.get("total_z"),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    top_rows = rows[:top]
    verdict = _verdict(top_rows, weights, drop)
    return {
        "drop": {"name": drop["name"], "team": drop["nba_team"], "positions": drop["positions"], "injury": drop_injury,
                 "games_left_this_week": drop["games_left"], "games_next_week": drop_next_games, "z_per_game": round(drop_z, 2),
                 "opportunity": round(drop_opp, 2), "mpg": drop.get("mpg"), "hashtag_id": drop.get("hashtag_id"),
                 "yahoo": dict(ctx.yahoo.get(drop["name_key"], {}))},
        "matchup": {"my_team": my_team, "opp_team": opp_team, "week": planner["week"], "days_left": days_left,
                    "baseline_win_probability": base_wp, "hashtag_win_probability": hashtag_wp, "hashtag_record": planner.get("record"),
                    "closeness_multiplier": round(closeness, 2), "calibrated_to_hashtag": scales is not None,
                    "categories": {c: {"prob": round(base_probs[c], 3), "hashtag_prob": ctx.hashtag_probs.get(c), "my": round(base_my[c], 3), "opp": round(base_opp[c], 3)} for c in ALL_CATEGORIES}},
        "weights": {"this_week": weights.this_week, "next_week": weights.next_week, "ros": weights.ros, "punt": punt},
        "pool_size": table["n"],
        "candidates": top_rows,
        "verdict": verdict,
    }


def _opportunity(player: Mapping[str, Any], recent_mpg: Mapping[str, float], weights: DecisionWeights) -> float:
    ros_mpg = float(player.get("mpg") or 0.0)
    recent = recent_mpg.get(player.get("name_key", ""))
    if not ros_mpg or recent is None or recent <= 0:
        return 1.0
    lo, hi = weights.opportunity_clamp
    return clamp(recent / ros_mpg, lo, hi)


def _verdict(rows: list[dict[str, Any]], weights: DecisionWeights, drop: Mapping[str, Any]) -> dict[str, str]:
    if not rows:
        return {"action": "HOLD", "reason": "No free agent improves on the dropped player."}
    best = rows[0]
    if best["score"] < weights.hold_threshold:
        return {"action": "HOLD", "reason": f"Best option ({best['add']}) scores {best['score']:+.1f}; not worth the churn."}
    if best["delta_ros_z"] >= 0 and best["delta_week_pts"] >= 0:
        return {"action": "DROP", "reason": f"{best['add']} is an upgrade now ({best['delta_week_pts']:+.1f} pts this week) and for the rest of the season ({best['delta_ros_z']:+.2f} z/game)."}
    if best["delta_week_pts"] > 0 and best["delta_ros_z"] < 0:
        if abs(best["delta_ros_z"]) <= weights.streaming_ros_tolerance:
            return {"action": "STREAM", "reason": f"{best['add']} helps this week ({best['delta_week_pts']:+.1f} pts) at a small rest-of-season cost ({best['delta_ros_z']:+.2f} z/game); re-check after the week."}
        return {"action": "MARGINAL", "reason": f"{best['add']} helps this week but costs real rest-of-season value ({best['delta_ros_z']:+.2f} z/game); only if the week matters more."}
    return {"action": "MARGINAL", "reason": f"{best['add']} is better long-term ({best['delta_ros_z']:+.2f} z/game) but not this week ({best['delta_week_pts']:+.1f} pts)."}


def next_week_range(week_start: date) -> tuple[date, date]:
    start = week_start + timedelta(days=7)
    return start, start + timedelta(days=6)


def build_context(client, schedule: NbaSchedule, today: date, projection_recent: str = "STREAM14", pool: int = 200,
                  use_cache: bool = True, with_recent: bool = True, yahoo: Optional[Mapping[str, Mapping[str, Any]]] = None) -> DecisionContext:
    """Fetch planner (ROS + recommendations), waiver wire, recent-form MPG and ESPN schedule."""
    planner = client.planner_with_recommendations(projection="ROS", use_cache=use_cache)
    free_agents = client.waiver_wire(projection="ROS", show=pool, use_cache=use_cache)
    recent_mpg: dict[str, float] = {}
    if with_recent:
        try:
            for p in client.waiver_wire(projection=projection_recent, show=pool, use_cache=use_cache):
                recent_mpg[p["name_key"]] = p["mpg"]
            for p in client.planner(projection=projection_recent, use_cache=use_cache)["roster"]:
                recent_mpg[p["name_key"]] = p["mpg"]
        except Exception as exc:  # recent form is an adjustment, not a requirement
            log.warning("Recent-form MPG unavailable: %s", exc)
    week_start = today - timedelta(days=today.weekday())
    nxt_start, nxt_end = next_week_range(week_start)
    nxt = schedule.games_by_team(nxt_start, nxt_end, today)
    rem = schedule.remaining_games(week_start, week_start + timedelta(days=6), today)
    return DecisionContext(planner=planner, free_agents=free_agents, today=today, week_start=week_start,
                           next_week_games=nxt["counts"], remaining_teams_by_day=rem["teams_by_day"], recent_mpg=recent_mpg,
                           yahoo=yahoo or {}, hashtag_probs=planner.get("prob", {}))
