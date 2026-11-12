"""Per-game stats, recent-form blends and remaining-week projections.

All functions are pure: they take player dicts (as produced by fbb.yahoo.parse)
and schedule counts, and return numbers. Nothing here talks to the network.

Player contribution dicts carry the 7 counting categories as week totals plus
FGM/FGA/FTM/FTA so percentages can be recombined across players.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .categories import ALL_CATEGORIES, COUNTING_CATEGORIES, VOLUME_KEYS, normalize_team
from .config import DEFAULT_PARAMS, ModelParams
from .util import clamp, safe_float

STATS_SOURCE_PER_GAME = {"average_season", "per_game_recent_2w"}


def normalize_to_per_game(stats: Mapping[str, float], stats_source: str, params: ModelParams = DEFAULT_PARAMS) -> dict[str, float]:
    """Return per-game values for the 9 categories (+ volume keys when present)."""
    out: dict[str, float] = {}
    if stats_source in STATS_SOURCE_PER_GAME:
        for cat in ALL_CATEGORIES:
            out[cat] = float(stats.get(cat, 0.0))
        for key in VOLUME_KEYS:
            if key in stats:
                out[key] = float(stats[key])
        return out
    games = params.assumed_games_played_for_totals
    for cat in COUNTING_CATEGORIES:
        out[cat] = float(stats.get(cat, 0.0)) / games
    for key in VOLUME_KEYS:
        if key in stats:
            out[key] = float(stats[key]) / games
    out["FG%"] = float(stats.get("FG%", 0.0))
    out["FT%"] = float(stats.get("FT%", 0.0))
    return out


def blend_recent(
    week_row: Optional[Mapping[str, Any]],
    month_row: Optional[Mapping[str, Any]],
    week_games_by_team: Mapping[str, int],
    month_games_by_team: Mapping[str, int],
    fallback_week_games: int,
    fallback_month_games: int,
    params: ModelParams = DEFAULT_PARAMS,
) -> dict[str, float]:
    """Approximate last-two-weeks per-game stats from Yahoo `lastweek` and `lastmonth` totals.

    Totals are divided by the team's game count in each window, then blended
    week-heavy. Percentages that are missing or zero in one window (a player
    with no attempts) take the other window's value instead of dragging the
    blend toward zero.
    """
    week_stats = dict((week_row or {}).get("stats", {}) or {})
    month_stats = dict((month_row or {}).get("stats", {}) or {})
    team = normalize_team((week_row or {}).get("nba_team", "") or (month_row or {}).get("nba_team", ""))
    week_games = max(float(week_games_by_team.get(team, fallback_week_games)), 1.0)
    month_games = max(float(month_games_by_team.get(team, fallback_month_games)), 1.0)
    has_week, has_month = bool(week_stats), bool(month_stats)

    if has_week and has_month:
        ww, mw = params.recent_week_weight, params.recent_month_weight
    elif has_week:
        ww, mw = 1.0, 0.0
    elif has_month:
        ww, mw = 0.0, 1.0
    else:
        ww, mw = 0.0, 1.0
        month_stats = {}

    out: dict[str, float] = {}
    for cat in COUNTING_CATEGORIES + VOLUME_KEYS:
        week_pg = (safe_float(week_stats.get(cat)) / week_games) if has_week and cat in week_stats else 0.0
        month_pg = (safe_float(month_stats.get(cat)) / month_games) if has_month and cat in month_stats else 0.0
        if cat in VOLUME_KEYS and cat not in week_stats and cat not in month_stats:
            continue
        out[cat] = week_pg * ww + month_pg * mw

    for cat, default in (("FG%", params.default_fg_pct), ("FT%", params.default_ft_pct)):
        wv = clamp(safe_float(week_stats.get(cat, default)), 0.0, 1.0) if has_week else 0.0
        mv = clamp(safe_float(month_stats.get(cat, default)), 0.0, 1.0) if has_month else 0.0
        if ww > 0 and mw > 0:
            if wv <= 0.0 and mv > 0.0:
                out[cat] = mv
            elif mv <= 0.0 and wv > 0.0:
                out[cat] = wv
            else:
                out[cat] = clamp(wv * ww + mv * mw, 0.0, 1.0)
        elif ww > 0:
            out[cat] = wv if wv > 0 else default
        else:
            out[cat] = mv if mv > 0 else default
    return out


def availability_multiplier(player: Mapping[str, Any], params: ModelParams = DEFAULT_PARAMS, include_news_context: bool = True) -> float:
    """Fraction of remaining games a player is expected to play, from Yahoo status and notes."""
    status = (player.get("status", "") or "").strip().upper()
    mult = params.status_multiplier.get(status, 1.0)
    if not include_news_context:
        return mult
    status_full = (player.get("status_full", "") or "").lower()
    news = (player.get("news", "") or "").lower()
    text = status_full + " " + news
    if "probable" in text and mult < params.news_probable_floor:
        mult = params.news_probable_floor
    elif "questionable" in text and mult < params.news_questionable_floor:
        mult = params.news_questionable_floor
    elif "day-to-day" in text and mult < params.news_day_to_day_floor:
        mult = params.news_day_to_day_floor
    if mult == 0.0 and (player.get("return_hint", "") or "").strip():
        mult = params.out_with_return_hint
    return mult


def games_remaining(player: Mapping[str, Any], team_game_counts: Mapping[str, int], fallback_games: float,
                    params: ModelParams = DEFAULT_PARAMS, include_news_context: bool = True) -> float:
    team = normalize_team(player.get("nba_team", ""))
    base = float(team_game_counts.get(team, fallback_games))
    return max(base * availability_multiplier(player, params, include_news_context), 0.0)


def estimate_volume(per_game: Mapping[str, float], params: ModelParams = DEFAULT_PARAMS) -> dict[str, float]:
    """Per-game FGM/FGA/FTM/FTA: real values when the data has them, else estimated from points."""
    fg_pct = clamp(per_game.get("FG%", params.default_fg_pct), 0.0, 1.0)
    ft_pct = clamp(per_game.get("FT%", params.default_ft_pct), 0.0, 1.0)
    if per_game.get("FGA", 0.0) > 0 and per_game.get("FTA", 0.0) > 0:
        return {"FGM": float(per_game.get("FGM", fg_pct * per_game["FGA"])), "FGA": float(per_game["FGA"]),
                "FTM": float(per_game.get("FTM", ft_pct * per_game["FTA"])), "FTA": float(per_game["FTA"])}
    pts = max(per_game.get("PTS", 0.0), 0.0)
    fga = (pts * params.fga_per_point) / max(fg_pct, params.fga_pct_floor) if pts > 0 else 0.0
    fta = (pts * params.fta_per_point) / max(ft_pct, params.fta_pct_floor) if pts > 0 else 0.0
    return {"FGM": fga * fg_pct, "FGA": fga, "FTM": fta * ft_pct, "FTA": fta}


def project_player(player: Mapping[str, Any], stats_source: str, team_game_counts: Mapping[str, int], fallback_games: float,
                   params: ModelParams = DEFAULT_PARAMS, include_news_context: bool = True) -> dict[str, Any]:
    """Remaining-week contribution for one player (week totals + volume + metadata)."""
    per_game = normalize_to_per_game(player.get("stats", {}), stats_source, params)
    games = games_remaining(player, team_game_counts, fallback_games, params, include_news_context)
    volume = estimate_volume(per_game, params)
    contribution: dict[str, Any] = {
        "player_key": player.get("player_key", ""),
        "player_id": int(safe_float(player.get("player_id", 0))),
        "name": player.get("name", ""),
        "position": player.get("position", ""),
        "nba_team": player.get("nba_team", ""),
        "status": player.get("status", ""),
        "status_full": player.get("status_full", ""),
        "news": player.get("news", ""),
        "return_hint": player.get("return_hint", ""),
        "percent_owned": safe_float(player.get("percent_owned", 0)),
        "is_undroppable": bool(player.get("is_undroppable", False)),
        "games_remaining": round(games, 2),
        "per_game": per_game,
    }
    for cat in COUNTING_CATEGORIES:
        contribution[cat] = per_game.get(cat, 0.0) * games
    for key in VOLUME_KEYS:
        contribution[key] = volume[key] * games
    return contribution


def empty_totals() -> dict[str, float]:
    return {cat: 0.0 for cat in COUNTING_CATEGORIES + VOLUME_KEYS}


def finalize_percentages(totals: dict[str, float]) -> dict[str, float]:
    totals["FG%"] = totals["FGM"] / totals["FGA"] if totals.get("FGA", 0.0) > 0 else 0.0
    totals["FT%"] = totals["FTM"] / totals["FTA"] if totals.get("FTA", 0.0) > 0 else 0.0
    return totals


def project_team(players: list[Mapping[str, Any]], stats_source: str, team_game_counts: Mapping[str, int], fallback_games: float,
                 params: ModelParams = DEFAULT_PARAMS, include_news_context: bool = True) -> tuple[dict[str, float], list[dict[str, Any]]]:
    totals = empty_totals()
    contributions = []
    for player in players:
        c = project_player(player, stats_source, team_game_counts, fallback_games, params, include_news_context)
        contributions.append(c)
        for cat in COUNTING_CATEGORIES + VOLUME_KEYS:
            totals[cat] += c[cat]
    return finalize_percentages(totals), contributions


def totals_shift(totals: Mapping[str, float], remove: Optional[Mapping[str, Any]] = None,
                 add: Optional[Mapping[str, Any]] = None) -> dict[str, float]:
    """`totals - remove + add` over counting and volume keys, percentages recomputed."""
    out = {cat: float(totals.get(cat, 0.0)) for cat in COUNTING_CATEGORIES + VOLUME_KEYS}
    for cat in COUNTING_CATEGORIES + VOLUME_KEYS:
        if remove:
            out[cat] -= float(remove.get(cat, 0.0))
        if add:
            out[cat] += float(add.get(cat, 0.0))
        out[cat] = max(out[cat], 0.0)
    return finalize_percentages(out)


def categories_to_totals(cat_values: Mapping[str, float], params: ModelParams = DEFAULT_PARAMS) -> dict[str, float]:
    """Banked week score (9 categories, optionally FGM/FGA/FTM/FTA) as extended totals."""
    totals = {cat: float(cat_values.get(cat, 0.0)) for cat in COUNTING_CATEGORIES}
    fg_pct = clamp(cat_values.get("FG%", params.default_fg_pct), 0.0, 1.0)
    ft_pct = clamp(cat_values.get("FT%", params.default_ft_pct), 0.0, 1.0)
    if cat_values.get("FGA", 0.0) > 0 and cat_values.get("FTA", 0.0) > 0:
        totals["FGA"] = float(cat_values["FGA"])
        totals["FGM"] = float(cat_values.get("FGM", fg_pct * totals["FGA"]))
        totals["FTA"] = float(cat_values["FTA"])
        totals["FTM"] = float(cat_values.get("FTM", ft_pct * totals["FTA"]))
    else:
        pts = max(totals["PTS"], 0.0)
        fga = (pts * params.fga_per_point) / max(fg_pct, params.fga_pct_floor) if pts > 0 else 0.0
        fta = (pts * params.fta_per_point) / max(ft_pct, params.fta_pct_floor) if pts > 0 else 0.0
        totals.update({"FGM": fga * fg_pct, "FGA": fga, "FTM": fta * ft_pct, "FTA": fta})
    totals["FG%"] = fg_pct
    totals["FT%"] = ft_pct
    return totals


def combine_totals(current: Mapping[str, float], remaining: Mapping[str, float]) -> dict[str, float]:
    combined = {cat: float(current.get(cat, 0.0)) + float(remaining.get(cat, 0.0)) for cat in COUNTING_CATEGORIES + VOLUME_KEYS}
    return finalize_percentages(combined)


def format_totals(totals: Mapping[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for cat in ALL_CATEGORIES:
        value = float(totals.get(cat, 0.0))
        out[cat] = round(value, 4) if cat in ("FG%", "FT%") else round(value, 2)
    return out
