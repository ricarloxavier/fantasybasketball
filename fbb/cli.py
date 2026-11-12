"""`fbb` command line: one entry point for every tool in the package.

    fbb auth            link (or re-link) the Yahoo account in a browser
    fbb whoami          league, your team, current week, matchup
    fbb waiver          waiver optimizer (same engine as the web page)
    fbb transactions    most added / dropped players
    fbb awards          Putilla awards JSON
    fbb roster ...      Hashtag Basketball roster decisions (see fbb.hashtag)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_PARAMS, Settings


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.from_env()


def _client(settings: Settings):
    from .yahoo.client import YahooClient
    return YahooClient.from_settings(settings, DEFAULT_PARAMS)


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_auth(args: argparse.Namespace) -> int:
    """Run Yahoo's browser consent flow and store tokens in the OAuth file."""
    from yahoo_oauth import OAuth2
    settings = _settings(args)
    path = Path(settings.yahoo_oauth_file)
    if not path.exists():
        print(f"{path} not found. Create it with your Yahoo app's consumer_key and consumer_secret first (see README).")
        return 2
    data = json.loads(path.read_text("utf-8"))
    if not data.get("consumer_key") or not data.get("consumer_secret"):
        print(f"{path} needs consumer_key and consumer_secret.")
        return 2
    backup = path.with_suffix(".json.bak")
    shutil.copy2(path, backup)
    stripped = {"consumer_key": data["consumer_key"], "consumer_secret": data["consumer_secret"]}
    path.write_text(json.dumps(stripped, indent=2), "utf-8")
    print("A browser window will open for Yahoo consent. Paste the verifier code back here.")
    print(f"(Previous tokens backed up to {backup}.)")
    OAuth2(None, None, from_file=str(path))  # runs the consent flow and writes tokens back
    print("Tokens saved. Checking access...")
    return cmd_whoami(args)


def cmd_whoami(args: argparse.Namespace) -> int:
    settings = _settings(args)
    client = _client(settings)
    meta = client.league_meta()
    teams = client.teams()
    team_key = client.team_key
    week = client.current_week()
    matchup = client.matchup(team_key, week)
    opp = matchup["opponent_team_key"]
    print(f"League:  {meta['name']} ({client.league_key}), season {meta['season']}, week {week} of {meta['end_week']}")
    print(f"Team:    {teams[team_key]['name']} ({team_key})")
    print(f"Matchup: vs {teams.get(opp, {}).get('name', '?')} ({opp}), {matchup['week_start']} to {matchup['week_end']}")
    print("Teams:   " + ", ".join(f"{v['name']} [{k.split('.t.')[-1]}]" for k, v in sorted(teams.items(), key=lambda kv: kv[1]['name'])))
    return 0


def cmd_waiver(args: argparse.Namespace) -> int:
    from .optimizer import WaiverOptions, get_waiver_recommendations
    settings = _settings(args)
    options = WaiverOptions(
        free_agent_pool=args.pool, drop_candidates=args.drop_candidates, max_results=args.top,
        punt_categories=args.punt or [], forced_drop_players=args.drop or [], never_drop_players=args.never_drop or [],
        projection_window=args.window, include_current_score=not args.remaining_only,
    )
    result = get_waiver_recommendations(settings, options, team_key=args.team or "")
    if args.json:
        _print_json(result)
        return 0
    d = result["data"]
    b = d["baseline"]
    print(f"Week {d['current_week']} ({d['week_start']} to {d['week_end']}): {d['my_team_name']} vs {d['opponent_team_name']}")
    print(f"Baseline win probability {b['win_probability']*100:.1f}%  projected score {b['deterministic_score']['wins']}-{b['deterministic_score']['losses']}"
          f"  days left {d['days_remaining']} (schedule: {d['games_source']}, stats: {d['stats_source']})")
    print("Categories: " + "  ".join(f"{c} {b['categories'][c]['prob_you_win']*100:.0f}%" for c in b["categories"]))
    print(f"\nDrop candidates ({d['settings']['drop_mode']}):")
    for c in d["drop_candidates"]:
        print(f"  {c['name']:<26} {c['team']:<4} {c['position']:<9} games left {c['games_remaining']:<4} owned {c['percent_owned']:>5}%  score {c['drop_score']}")
    print("\nTop moves:")
    print(f"  {'#':<3}{'add':<26}{'drop':<26}{'Δwin':>7} {'new':>6}  flips / reason")
    for r in d["recommendations"]:
        print(f"  {r['rank']:<3}{r['pickup_player'][:25]:<26}{r['drop_player'][:25]:<26}{r['delta_win_probability']*100:>+6.1f}% {r['new_win_probability']*100:>5.1f}%  "
              f"{','.join(r['flipped_categories']) or '-'} · {r['reason']}")
    return 0


def cmd_transactions(args: argparse.Namespace) -> int:
    from .transactions import tally_adds_drops, trends
    settings = _settings(args)
    client = _client(settings)
    txs = client.transactions("add,drop")
    tallies = tally_adds_drops(txs)
    if args.json:
        _print_json({"transactions": len(txs), "adds": tallies["adds"].most_common(args.top), "drops": tallies["drops"].most_common(args.top),
                     "trends": trends(tallies["adds"], tallies["drops"])})
        return 0
    print(f"{len(txs)} add/drop transactions in {client.league_key}\n")
    for title, counter in (("Most added", tallies["adds"]), ("Most dropped", tallies["drops"])):
        print(title)
        for i, (name, n) in enumerate(counter.most_common(args.top), 1):
            print(f"  {i:>2}. {name:<28} {n}")
        print()
    if args.trends:
        t = trends(tallies["adds"], tallies["drops"])
        for title, rows in (("Hottest (net adds)", t["hot"]), ("Cooling (net drops)", t["cold"])):
            print(title)
            for r in rows[:args.top]:
                print(f"  {r['player']:<28} adds {r['adds']:<3} drops {r['drops']:<3} net {r['net']:<3} ratio {r['ratio']}")
            print()
    return 0


def cmd_awards(args: argparse.Namespace) -> int:
    from .awards import compute_awards
    settings = _settings(args)
    result = compute_awards(_client(settings), settings, use_cache=not args.refresh)
    if args.json:
        _print_json(result)
        return 0
    print(f"Putilla awards, {result['league_name']} week {result['current_week']} ({result['total_stints']} stints), generated {result['generated_at']}\n")
    for group in ("team_awards", "player_awards"):
        for key, award in result[group].items():
            if not award:
                continue
            w = award["winner"]
            metric = next((k for k in ("games_started", "unique_players", "total_value", "value_per_game") if k in w), "")
            value = w.get(metric)
            value = f"{value:.2f}" if isinstance(value, float) else value
            who = w["team_name"] if w["player_name"] == "Team Total" else f"{w['player_name']} ({w['team_name']})"
            print(f"  {key:<22} {who:<40} {metric} {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fbb", description="Chantasy fantasy basketball tools")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="link your Yahoo account (browser consent)").set_defaults(func=cmd_auth)
    sub.add_parser("whoami", help="show league, team, week and matchup").set_defaults(func=cmd_whoami)

    w = sub.add_parser("waiver", help="rank add/drop moves for the current matchup")
    w.add_argument("--team", help="team key to analyze (default: yours)")
    w.add_argument("--window", choices=["recent_2w", "season"], default="recent_2w")
    w.add_argument("--pool", type=int, default=DEFAULT_PARAMS.default_free_agent_pool, help="free-agent pool size")
    w.add_argument("--drop-candidates", type=int, default=DEFAULT_PARAMS.default_drop_candidates)
    w.add_argument("--top", type=int, default=DEFAULT_PARAMS.default_max_results)
    w.add_argument("--punt", action="append", help="category to ignore (repeatable)")
    w.add_argument("--drop", action="append", help="force this player as the drop (repeatable)")
    w.add_argument("--never-drop", action="append", help="protect this player in auto mode (repeatable)")
    w.add_argument("--remaining-only", action="store_true", help="ignore the banked score this week")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_waiver)

    t = sub.add_parser("transactions", help="most added and dropped players")
    t.add_argument("--top", type=int, default=20)
    t.add_argument("--trends", action="store_true")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=cmd_transactions)

    a = sub.add_parser("awards", help="Putilla awards")
    a.add_argument("--refresh", action="store_true", help="ignore the cached result")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_awards)

    try:
        from .hashtag.cli import add_subcommands as add_hashtag_subcommands
        add_hashtag_subcommands(sub)
    except ImportError:
        pass
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # the CLI is the boundary: report and exit non-zero
        if args.verbose:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
