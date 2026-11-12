"""`fbb roster ...` subcommands backed by Hashtag Basketball."""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from ..cache import TTLCache
from ..config import Settings
from ..schedule import NbaSchedule
from ..util import today_in_tz
from .decision import DecisionWeights as _DW

log = logging.getLogger(__name__)


def _client(settings: Settings):
    from .client import HashtagClient
    return HashtagClient.from_settings(settings)


def _schedule(settings: Settings) -> NbaSchedule:
    return NbaSchedule(cache=TTLCache(settings.cache_dir, "espn"))


def cmd_roster_whoami(args) -> int:
    settings = Settings.from_env()
    p = _client(settings).planner(use_cache=not args.no_cache)
    print(f"Hashtag league {settings.hashtag_league_id}: {p['my_team']} (team {p['my_team_value']}) vs {p['opp_team']} (team {p['opp_team_value']}), week {p['week']}")
    print("Teams: " + ", ".join(f"{t} [{v}]" for v, t in p["teams"] if v != "999"))
    return 0


def cmd_roster_cleanup(args) -> int:
    removed = _client(Settings.from_env()).remove_added_players()
    print("Removed from planner sandbox: " + (", ".join(removed) if removed else "nothing to remove"))
    return 0


def cmd_roster_matchup(args) -> int:
    settings = Settings.from_env()
    p = _client(settings).planner(week=args.week, projection=args.projection, use_cache=not args.no_cache)
    if args.json:
        p.pop("_state", None)
        print(json.dumps(p, indent=2, default=str))
        return 0
    my, opp = p["team_order"]
    print(f"Week {p['week']} · {my} vs {opp} · projection {args.projection} · days played {p['days_played']}")
    cats = ["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"]
    for label, block in (("BEFORE", p["before"]), ("AFTER", p["after"])):
        print(f"{label:<8}" + "".join(f"{c:>8}" for c in cats))
        for team in (my, opp):
            print(f"{team[:8]:<8}" + "".join(f"{block[team].get(c, 0):>8}" for c in cats))
    print(f"{'PROB':<8}" + "".join(f"{p['prob'].get(c, 0)*100:>7.0f}%" for c in cats) + f"   record {p['record']}")
    print("\nRoster (games left this week in brackets):")
    for r in p["roster"]:
        games = " ".join(f"{g['day'][:2]}:{g['opponent']}{'' if g['counted'] else '(x)'}" for g in r["games"])
        flag = f" [{r['injury']}]" if r["injury"] else ""
        print(f"  {r['name']:<24}{'/'.join(r['positions']):<9}{r['nba_team']:<4} mpg {r['mpg']:<5} pts {r['stats']['PTS']:<5} reb {r['stats']['REB']:<5} ast {r['stats']['AST']:<5} [{r['games_left']}] {games}{flag}")
    if p["recommendations"]:
        print("\nHashtag's own pickup suggestions:")
        for r in p["recommendations"]:
            print(f"  {r['name']:<24}{r['nba_team']:<4} this week {','.join(r['games_this_week']) or '-'} (fits {','.join(r['fit_days']) or '-'}) next week {len(r['games_next_week'])} games")
    return 0


def cmd_roster_free_agents(args) -> int:
    settings = Settings.from_env()
    rows = _client(settings).waiver_wire(projection=args.projection, show=max(50, args.top), use_cache=not args.no_cache)[:args.top]
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    print(f"{'R#':<5}{'player':<26}{'pos':<10}{'tm':<5}{'gp':<4}{'mpg':<6}{'fg%':<7}{'ft%':<7}{'3pm':<5}{'pts':<6}{'reb':<5}{'ast':<5}{'stl':<5}{'blk':<5}{'to':<5}{'z':<6}")
    for r in rows:
        s = r["stats"]
        inj = f" [{r['injury']}]" if r["injury"] else ""
        print(f"{r['rank']:<5}{(r['name'] + inj)[:25]:<26}{'/'.join(r['positions'])[:9]:<10}{r['nba_team']:<5}{r['gp'] or '':<4}{r['mpg']:<6}{s['FG%']:<7}{s['FT%']:<7}{s['3PTM']:<5}{s['PTS']:<6}{s['REB']:<5}{s['AST']:<5}{s['ST']:<5}{s['BLK']:<5}{s['TO']:<5}{r['total_z']:<6}")
    return 0


def cmd_roster_drop(args) -> int:
    from .decision import DecisionWeights, build_context, evaluate_drop
    settings = Settings.from_env()
    client = _client(settings)
    today = today_in_tz(settings.timezone)
    yahoo = {}
    if not args.no_yahoo:
        try:
            yahoo = _yahoo_signals(settings)
        except Exception as exc:
            log.warning("Yahoo cross-check unavailable: %s", exc)
    ctx = build_context(client, _schedule(settings), today, pool=args.pool, use_cache=not args.no_cache, with_recent=not args.no_recent, yahoo=yahoo)
    weights = DecisionWeights(this_week=args.w0, next_week=args.w1, ros=args.wros, punt=args.punt or [])
    reports = []
    for name in args.drop:
        reports.append(evaluate_drop(ctx, name, weights, top=args.top))
    if args.exact:
        for rep in reports:
            _exact_check(client, ctx, rep, args)
    if args.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0], indent=2, default=str))
        return 0
    for rep in reports:
        _print_report(rep)
    return 0


def _yahoo_signals(settings: Settings) -> dict[str, dict[str, Any]]:
    from ..categories import name_key
    from ..yahoo.client import YahooClient
    client = YahooClient.from_settings(settings)
    out: dict[str, dict[str, Any]] = {}
    roster = client.roster(client.team_key)
    for p in client.player_details([r["player_key"] for r in roster]).values():
        out[name_key(p["name"])] = {"status": p.get("status", ""), "status_full": p.get("status_full", ""), "is_free_agent": False}
    for p in client.free_agents(200):
        out[name_key(p["name"])] = {"status": p.get("status", ""), "status_full": p.get("status_full", ""), "is_free_agent": True}
    return out


def _exact_check(client, ctx, rep: dict[str, Any], args) -> None:
    """Re-post the planner with the drop's games cleared and each top candidate added; report Hashtag's own odds."""
    from ..matchup import matchup_win_probability
    recs = {r["name_key"]: r for r in ctx.planner.get("recommendations", [])}
    from ..categories import name_key
    for cand in rep["candidates"][:args.exact]:
        rec = recs.get(name_key(cand["add"]))
        if not rec or not rec.get("add_target"):
            cand["exact"] = "not in Hashtag's suggestion list"
            continue
        try:
            out = client.planner_scenario(drop_hashtag_ids=[rep["drop"]["hashtag_id"]], add_target=rec["add_target"], use_cache=not args.no_cache)
            wp = matchup_win_probability(out["prob"], rep["weights"]["punt"])
            base = rep["matchup"]["hashtag_win_probability"]
            my, opp = out["team_order"][0], out["team_order"][1]
            cand["exact"] = {"hashtag_record": out["record"], "hashtag_win_probability": wp, "delta_pts": round((wp - base) * 100, 1) if base is not None else None,
                             "categories": {c: round(p, 2) for c, p in out["prob"].items()}, "after_my": out["after"][my], "after_opp": out["after"][opp]}
        except Exception as exc:
            cand["exact"] = f"failed: {exc}"


def _print_report(rep: dict[str, Any]) -> None:
    d, m = rep["drop"], rep["matchup"]
    print(f"Week {m['week']} · {m['my_team']} vs {m['opp_team']} · days left {','.join(m['days_left'])}")
    hp = m["hashtag_win_probability"]
    print(f"Baseline win probability: model {m['baseline_win_probability']*100:.0f}%  ·  Hashtag {hp*100:.0f}% (record {m['hashtag_record']})" if hp is not None else
          f"Baseline win probability: model {m['baseline_win_probability']*100:.0f}%")
    print("Categories (model / Hashtag): " + "  ".join(f"{c} {v['prob']*100:.0f}/{(v['hashtag_prob'] or 0)*100:.0f}" for c, v in m["categories"].items()))
    inj = f" · {d['injury']}" if d["injury"] else ""
    print(f"\nDrop candidate: {d['name']} ({'/'.join(d['positions'])}-{d['team']}){inj} · {d['games_left_this_week']} game(s) left this week, {d['games_next_week']} next week · "
          f"value/game {d['z_per_game']:+.2f} · opportunity x{d['opportunity']} · mpg {d['mpg']}")
    print(f"Weights: this week x{rep['weights']['this_week']} (closeness x{m['closeness_multiplier']}), next week {rep['weights']['next_week']}/value-game, ROS {rep['weights']['ros']}/value"
          + (f" · punting {','.join(rep['weights']['punt'])}" if rep["weights"]["punt"] else "") + f" · pool {rep['pool_size']} players")
    print(f"\n{'add':<24}{'tm':<4}{'now':>4}{'next':>5}{'val/g':>6}{'Δweek':>7}{'Δnext':>7}{'ΔROS':>7}{'score':>7}  flips / notes")
    for c in rep["candidates"]:
        flips = ",".join(c["flips"]) or "-"
        exact = c.get("exact")
        exact_txt = ""
        if isinstance(exact, dict):
            dp = exact.get("delta_pts")
            exact_txt = f" · Hashtag exact {exact['hashtag_record']}" + (f" ({dp:+.1f} pts)" if dp is not None else "")
        elif exact:
            exact_txt = f" · exact: {exact}"
        notes = "; ".join(n[:60] for n in c["notes"])
        print(f"{c['add'][:23]:<24}{c['add_team']:<4}{c['games_this_week']:>4}{c['games_next_week']:>5}{c['z_per_game']:>+6.2f}{c['delta_week_pts']:>+7.1f}{c['delta_next_week_pts']:>+7.1f}{c['delta_ros_pts']:>+7.1f}{c['score']:>+7.1f}  {flips}{exact_txt}{(' · ' + notes) if notes else ''}")
    v = rep["verdict"]
    print(f"\nVerdict: {v['action']} — {v['reason']}")


def add_subcommands(sub: argparse._SubParsersAction) -> None:
    r = sub.add_parser("roster", help="roster decisions from Hashtag Basketball (premium)")
    rs = r.add_subparsers(dest="roster_command", required=True)

    cl = rs.add_parser("cleanup", help="remove players added to the planner sandbox (X links)")
    cl.set_defaults(func=cmd_roster_cleanup)

    w = rs.add_parser("whoami", help="league and team as Hashtag sees them")
    w.add_argument("--no-cache", action="store_true")
    w.set_defaults(func=cmd_roster_whoami)

    mu = rs.add_parser("matchup", help="planner summary: totals, probabilities, roster schedule")
    mu.add_argument("--week", type=int)
    mu.add_argument("--projection", default="ROS", help="ROS, BLEND7/14/30, STREAM7/14/30, SEASON")
    mu.add_argument("--no-cache", action="store_true")
    mu.add_argument("--json", action="store_true")
    mu.set_defaults(func=cmd_roster_matchup)

    fa = rs.add_parser("free-agents", help="Hashtag waiver wire rankings")
    fa.add_argument("top", type=int, nargs="?", default=30)
    fa.add_argument("--projection", default="ROS")
    fa.add_argument("--no-cache", action="store_true")
    fa.add_argument("--json", action="store_true")
    fa.set_defaults(func=cmd_roster_free_agents)

    dr = rs.add_parser("drop", help="should I drop PLAYER, and for whom")
    dr.add_argument("drop", nargs="+", help="player name(s) on your roster")
    dr.add_argument("--top", type=int, default=8)
    dr.add_argument("--pool", type=int, default=200, help="free agents considered")
    dr.add_argument("--punt", action="append")
    dr.add_argument("--w0", type=float, default=_DW.this_week, help="weight per win-probability point this week")
    dr.add_argument("--w1", type=float, default=_DW.next_week, help="points per z-unit per game next week")
    dr.add_argument("--wros", type=float, default=_DW.ros, help="points per z-unit of rest-of-season per-game value")
    dr.add_argument("--exact", type=int, nargs="?", const=3, default=0, help="verify the top N with Hashtag's own planner (postbacks)")
    dr.add_argument("--no-recent", action="store_true", help="skip the recent-form (STREAM14) minutes adjustment")
    dr.add_argument("--no-yahoo", action="store_true", help="skip the Yahoo status/free-agent cross-check")
    dr.add_argument("--no-cache", action="store_true")
    dr.add_argument("--json", action="store_true")
    dr.set_defaults(func=cmd_roster_drop)
