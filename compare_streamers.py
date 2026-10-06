#!/usr/bin/env python3
"""
Streamer comparison: find the best 2-player combo to replace
Walter Clayton Jr. (MEM, 1G) + Jaylon Tyson (CLE, 1G).

Evaluates all pairs of FAs on 2+ game teams and ranks by projected
category wins (banked + remaining recent-form projection).
"""

import sys
from datetime import datetime, timedelta
from itertools import combinations
from typing import Dict, List, Tuple

from weekly_streaming_analysis import (
    ensure_oauth, fetch_live_week_score, get_player_stats_typed,
    blend_recent_per_game, fetch_week_schedule, normalize_team, safe_float,
    analyze_categories, count_wins, get_team_roster, filter_active_players,
    calculate_weekly_projections, get_player_stats, api_get,
    LEAGUE_KEY, MY_TEAM_KEY, CATEGORIES, GAMES_PLAYED, PUNT_CATEGORIES,
    NBA_TEAM_CODES,
)
from yahoo_fantasy_api import League

DROP_PLAYERS  = ['Walter Clayton Jr.', 'Jaylon Tyson']
PUNT_CATS     = ['TO']
MIN_GAMES_REQ = 2   # include 2-game teams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_free_agents(oauth, pool_size: int = 250) -> Dict[str, Dict]:
    """Paginate Yahoo FA list (25 per request)."""
    results = {}
    for start in range(0, pool_size, 25):
        url = (f'https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_KEY}'
               f'/players;status=A;start={start};count=25?format=json')
        try:
            data = api_get(oauth, url)
            players = data['fantasy_content']['league'][1]['players']
        except Exception as e:
            print(f"   FA fetch failed at start={start}: {e}")
            break
        fetched = 0
        for k, v in players.items():
            if k == 'count':
                continue
            p = v['player'][0]
            player_key = name = team = position = status = ''
            for item in p:
                if not isinstance(item, dict):
                    continue
                if 'player_key' in item:
                    player_key = item['player_key']
                if 'name' in item:
                    name = item['name']['full']
                if 'editorial_team_abbr' in item:
                    team = normalize_team(item['editorial_team_abbr'])
                if 'display_position' in item:
                    position = item['display_position']
                if 'status' in item:
                    status = item['status']
            if player_key:
                results[player_key] = {'name': name, 'nba_team': team,
                                       'position': position, 'status': status}
                fetched += 1
        if fetched == 0:
            break
    return results


def add_banked(live: Dict, remaining: Dict) -> Dict:
    """Combine live banked stats + remaining projection into full-week totals."""
    combined: Dict[str, float] = {}
    for cat in ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM', 'TO']:
        combined[cat] = safe_float(live.get(cat, 0)) + remaining.get(cat, 0)
    live_pts = safe_float(live.get('PTS', 0))
    live_fg  = safe_float(live.get('FG%', 0))
    live_ft  = safe_float(live.get('FT%', 0))
    fga_live = (live_pts * 0.55) / max(live_fg, 0.25) if live_pts > 0 and live_fg > 0 else 0.0
    fta_live = (live_pts * 0.18) / max(live_ft, 0.40) if live_pts > 0 and live_ft > 0 else 0.0
    fgm = fga_live * live_fg + remaining.get('FGM', 0)
    fga = fga_live            + remaining.get('FGA', 0)
    ftm = fta_live * live_ft  + remaining.get('FTM', 0)
    fta = fta_live             + remaining.get('FTA', 0)
    combined['FG%'] = fgm / fga if fga > 0 else 0.0
    combined['FT%'] = ftm / fta if fta > 0 else 0.0
    return combined


def player_contribution(pg: Dict[str, float], games: int) -> Dict[str, float]:
    """Per-game rates × games → remaining contribution dict."""
    fg_pct = pg.get('FG%', 0.45)
    ft_pct = pg.get('FT%', 0.75)
    pts_pg = pg.get('PTS', 0)
    fga_pg = (pts_pg * 0.6) / 2 / max(fg_pct, 0.3) if fg_pct > 0 else 0
    fta_pg = (pts_pg * 0.15) / max(ft_pct, 0.3) if ft_pct > 0 else 0
    return {
        'PTS':  pts_pg              * games,
        'REB':  pg.get('REB', 0)    * games,
        'AST':  pg.get('AST', 0)    * games,
        'ST':   pg.get('ST',  0)    * games,
        'BLK':  pg.get('BLK', 0)    * games,
        '3PTM': pg.get('3PTM', 0)   * games,
        'TO':   pg.get('TO',  0)    * games,
        'FGM':  fga_pg * fg_pct     * games,
        'FGA':  fga_pg              * games,
        'FTM':  fta_pg * ft_pct     * games,
        'FTA':  fta_pg              * games,
    }


def add_rem(r1: Dict, r2: Dict) -> Dict:
    out = dict(r1)
    for k, v in r2.items():
        out[k] = out.get(k, 0) + v
    return out


def score_analysis(analysis: Dict, punt_cats: List[str]) -> Tuple[int, int, float]:
    """Return (wins, losses, margin_score) for sorting."""
    wins = losses = 0
    margin = 0.0
    for cat, d in analysis.items():
        if cat in punt_cats or d.get('is_punt'):
            continue
        if d['winner'] == 'YOU':
            wins += 1
            # Normalise margin contribution
            opp = abs(d['opp_value']) or 1
            margin += d['difference'] / opp
        elif d['winner'] == 'OPP':
            losses += 1
            opp = abs(d['opp_value']) or 1
            margin -= abs(d['difference']) / opp
    return wins, losses, margin


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("\n" + "=" * 80)
    print("  DUAL-STREAMER COMPARISON")
    print(f"  Dropping: {' + '.join(DROP_PLAYERS)}")
    print("  Using: live banked score + recent form × remaining games")
    print("=" * 80)

    oauth  = ensure_oauth()
    lg     = League(oauth, LEAGUE_KEY)

    current_week          = lg.current_week()
    week_start, week_end  = lg.week_date_range(current_week)
    today                 = datetime.now().date()
    remaining_start       = max(today, week_start)

    print(f"\n📅 Week {current_week}  ({week_start} → {week_end})  |  Today: {today}")

    # --- Schedule ---
    print("\n📡 Fetching remaining schedule from ESPN...")
    raw_schedule = fetch_week_schedule(remaining_start, week_end)
    lookback_end      = today - timedelta(days=1)
    week_game_counts  = fetch_week_schedule(lookback_end - timedelta(days=6),  lookback_end)
    month_game_counts = fetch_week_schedule(lookback_end - timedelta(days=29), lookback_end)

    # Zero out unlisted teams (avoid default=3 fallback)
    remaining_schedule: Dict[str, int] = {t: 0 for t in NBA_TEAM_CODES}
    remaining_schedule.update(raw_schedule)

    by_games: Dict[int, List[str]] = {}
    for t, g in remaining_schedule.items():
        if g > 0:
            by_games.setdefault(g, []).append(t)
    for g in sorted(by_games, reverse=True):
        print(f"   {g}G teams: {', '.join(sorted(by_games[g]))}")

    eligible_teams = sorted(t for t, g in remaining_schedule.items() if g >= MIN_GAMES_REQ)

    # --- Live banked scores ---
    my_team_obj  = lg.to_team(MY_TEAM_KEY)
    opp_team_key = my_team_obj.matchup(current_week)

    print("\n📊 Fetching live banked scores...")
    my_live  = fetch_live_week_score(oauth, MY_TEAM_KEY,  current_week)
    opp_live = fetch_live_week_score(oauth, opp_team_key, current_week)

    live_cats = ['PTS', 'REB', 'AST', 'ST', 'BLK', '3PTM']
    print(f"   YOU : " + "  ".join(f"{c}={my_live.get(c,0):.0f}"  for c in live_cats)
          + f"  FG%={my_live.get('FG%',0):.3f}  FT%={my_live.get('FT%',0):.3f}")
    print(f"   OPP : " + "  ".join(f"{c}={opp_live.get(c,0):.0f}" for c in live_cats)
          + f"  FG%={opp_live.get('FG%',0):.3f}  FT%={opp_live.get('FT%',0):.3f}")

    # --- Rosters + recent stats ---
    print("\n📥 Fetching rosters and recent stats...")
    my_roster_raw = get_team_roster(oauth, MY_TEAM_KEY)
    my_roster     = filter_active_players(my_roster_raw)
    opp_roster    = filter_active_players(get_team_roster(oauth, opp_team_key))

    my_keys  = [f"466.p.{p['player_id']}" for p in my_roster]
    opp_keys = [f"466.p.{p['player_id']}" for p in opp_roster]
    all_keys = list(set(my_keys + opp_keys))

    week_data   = get_player_stats_typed(oauth, all_keys, 'lastweek')
    month_data  = get_player_stats_typed(oauth, all_keys, 'lastmonth')
    season_data = get_player_stats(oauth, all_keys)

    nba_team_map: Dict[str, str] = {}
    for src in [season_data, month_data, week_data]:
        for pk, d in src.items():
            if not nba_team_map.get(pk):
                nba_team_map[pk] = normalize_team(d.get('nba_team', ''))

    pg_override: Dict[str, Dict] = {}
    for pk in all_keys:
        team = nba_team_map.get(pk, '')
        wg = max(float(week_game_counts.get(team, 3)), 1.0)
        mg = max(float(month_game_counts.get(team, 12)), 1.0)
        ws = week_data.get(pk, {}).get('stats', {})
        ms = month_data.get(pk, {}).get('stats', {})
        ss = season_data.get(pk, {}).get('stats', {})
        if ws or ms:
            pg_override[pk] = blend_recent_per_game(ws, ms, wg, mg)
        elif ss:
            pg_override[pk] = {cat: safe_float(ss.get(cat, 0)) / GAMES_PLAYED
                               for cat in ['PTS','REB','AST','ST','BLK','3PTM','TO']}
            pg_override[pk]['FG%'] = safe_float(ss.get('FG%', 0))
            pg_override[pk]['FT%'] = safe_float(ss.get('FT%', 0))

    # --- Remaining projections ---
    # Baseline: team with BOTH drops removed
    my_rem_nodrop, _ = calculate_weekly_projections(
        season_data, my_roster, exclude_names=DROP_PLAYERS,
        schedule=remaining_schedule, per_game_override=pg_override
    )
    opp_rem, _ = calculate_weekly_projections(
        season_data, opp_roster, schedule=remaining_schedule, per_game_override=pg_override
    )

    opp_totals   = add_banked(opp_live, opp_rem)
    my_nodrop    = add_banked(my_live, my_rem_nodrop)
    base_analysis = analyze_categories(my_nodrop, opp_totals, PUNT_CATS)
    base_wins, base_losses = count_wins(base_analysis, PUNT_CATS)

    print(f"\n📊 Baseline (WITHOUT {' & '.join(DROP_PLAYERS)}): {base_wins}W-{base_losses}L")
    for cat in CATEGORIES:
        d = base_analysis[cat]
        sym = '✅' if d['winner'] == 'YOU' else ('❌' if d['winner'] == 'OPP' else '🟡')
        if cat in ['FG%', 'FT%']:
            print(f"   {sym} {cat}: YOU {d['my_value']:.3f}  vs OPP {d['opp_value']:.3f}  ({d['difference']:+.3f})")
        else:
            print(f"   {sym} {cat}: YOU {d['my_value']:.1f}  vs OPP {d['opp_value']:.1f}  ({d['difference']:+.1f})")

    # --- Free agents on eligible teams ---
    print(f"\n🔍 Fetching free agents on {MIN_GAMES_REQ}+ game teams...")
    fa_all = fetch_free_agents(oauth, pool_size=250)
    fa_pool = {pk: info for pk, info in fa_all.items()
               if info['nba_team'] in eligible_teams
               and info['status'] not in ('O', 'INJ', 'IR', 'NA')}
    print(f"   Found {len(fa_pool)} eligible FAs  (teams: {', '.join(eligible_teams)})")

    if len(fa_pool) < 2:
        print("   Not enough candidates!")
        return

    # Recent stats for FA candidates
    fa_keys = list(fa_pool.keys())
    fa_week  = get_player_stats_typed(oauth, fa_keys, 'lastweek')
    fa_month = get_player_stats_typed(oauth, fa_keys, 'lastmonth')
    fa_season = get_player_stats(oauth, fa_keys)

    fa_pg: Dict[str, Dict] = {}
    for pk, info in fa_pool.items():
        team = info['nba_team']
        wg = max(float(week_game_counts.get(team, 3)), 1.0)
        mg = max(float(month_game_counts.get(team, 12)), 1.0)
        ws = fa_week.get(pk, {}).get('stats', {})
        ms = fa_month.get(pk, {}).get('stats', {})
        ss = fa_season.get(pk, {}).get('stats', {})
        if ws or ms:
            fa_pg[pk] = blend_recent_per_game(ws, ms, wg, mg)
        elif ss:
            fa_pg[pk] = {cat: safe_float(ss.get(cat, 0)) / GAMES_PLAYED
                         for cat in ['PTS','REB','AST','ST','BLK','3PTM','TO']}
            fa_pg[pk]['FG%'] = safe_float(ss.get('FG%', 0))
            fa_pg[pk]['FT%'] = safe_float(ss.get('FT%', 0))
        else:
            fa_pg[pk] = {}

    # Pre-compute each candidate's remaining contribution
    candidates = []
    for pk, info in fa_pool.items():
        pg = fa_pg.get(pk, {})
        if not pg or pg.get('PTS', 0) < 1.0:
            continue
        team  = info['nba_team']
        games = remaining_schedule.get(team, 0)
        contrib = player_contribution(pg, games)
        candidates.append({
            'pk': pk, 'name': info['name'], 'team': team, 'games': games,
            'contrib': contrib, 'pg': pg,
        })

    print(f"   {len(candidates)} candidates with usable stats")

    # --- Evaluate all pairs ---
    print(f"\n   Evaluating {len(list(combinations(range(len(candidates)), 2)))} pairs...")

    pair_results = []
    for i, j in combinations(range(len(candidates)), 2):
        c1, c2 = candidates[i], candidates[j]
        combined_rem = add_rem(add_rem(my_rem_nodrop, c1['contrib']), c2['contrib'])
        my_totals    = add_banked(my_live, combined_rem)
        analysis     = analyze_categories(my_totals, opp_totals, PUNT_CATS)
        wins, losses, margin = score_analysis(analysis, PUNT_CATS)
        total_games  = c1['games'] + c2['games']
        pair_results.append({
            'c1': c1, 'c2': c2, 'wins': wins, 'losses': losses,
            'margin': margin, 'total_games': total_games, 'analysis': analysis,
        })

    # Sort: wins desc, losses asc, then total_games desc, then margin desc
    pair_results.sort(key=lambda x: (x['wins'], -x['losses'], x['total_games'], x['margin']),
                      reverse=True)

    # --- Print top 10 pairs ---
    print(f"\n{'=' * 80}")
    print(f"  TOP STREAMING PAIRS  (drop {' + '.join(DROP_PLAYERS)})")
    print(f"{'=' * 80}")

    for rank, r in enumerate(pair_results[:10], 1):
        c1, c2 = r['c1'], r['c2']
        analysis = r['analysis']
        cats_won  = [cat for cat in CATEGORIES
                     if analysis[cat].get('winner') == 'YOU' and not analysis[cat].get('is_punt')]
        cats_lost = [cat for cat in CATEGORIES
                     if analysis[cat].get('winner') == 'OPP' and not analysis[cat].get('is_punt')]

        print(f"\n#{rank}  {c1['name']} ({c1['team']},{c1['games']}G) + "
              f"{c2['name']} ({c2['team']},{c2['games']}G)  "
              f"= {r['total_games']} games  →  {r['wins']}W-{r['losses']}L")
        print(f"     {c1['name']}: {c1['pg'].get('PTS',0):.1f}pts {c1['pg'].get('REB',0):.1f}reb "
              f"{c1['pg'].get('AST',0):.1f}ast {c1['pg'].get('ST',0):.1f}stl "
              f"{c1['pg'].get('BLK',0):.1f}blk {c1['pg'].get('3PTM',0):.1f}3pm "
              f"FG%={c1['pg'].get('FG%',0):.3f}")
        print(f"     {c2['name']}: {c2['pg'].get('PTS',0):.1f}pts {c2['pg'].get('REB',0):.1f}reb "
              f"{c2['pg'].get('AST',0):.1f}ast {c2['pg'].get('ST',0):.1f}stl "
              f"{c2['pg'].get('BLK',0):.1f}blk {c2['pg'].get('3PTM',0):.1f}3pm "
              f"FG%={c2['pg'].get('FG%',0):.3f}")
        # Show close categories with margins
        print(f"     ✅ WIN:  {', '.join(cats_won)}")
        print(f"     ❌ LOSS: {', '.join(cats_lost)}")
        # Show margins for won categories
        margin_parts = []
        for cat in cats_won + cats_lost:
            d = analysis[cat]
            if cat in ['FG%','FT%']:
                margin_parts.append(f"{cat}{d['difference']:+.3f}")
            else:
                margin_parts.append(f"{cat}{d['difference']:+.1f}")
        print(f"     Margins: {' | '.join(margin_parts)}")

    print(f"\n{'=' * 80}")
    print(f"  SUMMARY")
    print(f"{'=' * 80}")
    print(f"  Baseline (no drops): {base_wins}W-{base_losses}L")
    if pair_results:
        best = pair_results[0]
        c1, c2 = best['c1'], best['c2']
        print(f"  Best pair: {c1['name']} ({c1['team']},{c1['games']}G) + "
              f"{c2['name']} ({c2['team']},{c2['games']}G) = {best['total_games']} games")
        print(f"  Result: {best['wins']}W-{best['losses']}L  "
              f"(+{best['wins']-base_wins} vs baseline)")

    # Also show best single-player options for comparison
    print(f"\n  ── BEST SINGLE ADDS (for reference) ──")
    single_results = []
    for c in candidates:
        combined_rem = add_rem(my_rem_nodrop, c['contrib'])
        my_totals    = add_banked(my_live, combined_rem)
        analysis     = analyze_categories(my_totals, opp_totals, PUNT_CATS)
        wins, losses, margin = score_analysis(analysis, PUNT_CATS)
        single_results.append((wins, losses, margin, c))
    single_results.sort(key=lambda x: (x[0], -x[1], x[2]), reverse=True)
    for wins, losses, margin, c in single_results[:5]:
        print(f"  {c['name']} ({c['team']},{c['games']}G): {wins}W-{losses}L")
    print()


if __name__ == '__main__':
    main()
