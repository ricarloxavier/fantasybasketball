"""Category win probabilities and head-to-head matchup win probability."""

from __future__ import annotations

from typing import Any, Mapping

from .categories import ALL_CATEGORIES, PERCENT_CATEGORIES, REVERSE_CATEGORIES
from .config import DEFAULT_PARAMS, ModelParams
from .util import clamp, sigmoid


def category_win_prob(my_val: float, opp_val: float, cat: str, params: ModelParams = DEFAULT_PARAMS,
                      remaining_share: float = 1.0) -> float:
    """Probability of winning one category from projected end-of-week values.

    `remaining_share` is the fraction of the week still to be played (1 = whole
    week ahead, 0 = all games done). With `params.remaining_games_scaling > 0`
    the uncertainty shrinks as the week runs out, so a small lead with nothing
    left to play is close to a sure thing instead of ~55%.
    """
    if cat in PERCENT_CATEGORIES:
        raw = my_val - opp_val
        scale = params.pct_edge_scale
    else:
        raw = (opp_val - my_val) if cat in REVERSE_CATEGORIES else (my_val - opp_val)
        scale = (max(abs(my_val), abs(opp_val), 1.0) * params.counting_scale_ratio) + params.counting_scale_floor
    if params.remaining_games_scaling > 0:
        share = clamp(remaining_share, 0.0, 1.0)
        # Full scaling (1.0): spread shrinks linearly with the share of the week left, floor 10%.
        factor = 1.0 - params.remaining_games_scaling * (1.0 - share)
        scale *= max(factor, 0.1)
    return clamp(sigmoid(raw / scale), params.prob_clamp_lo, params.prob_clamp_hi)


def matchup_win_probability(category_probs: Mapping[str, float], punt_categories: list[str]) -> float:
    """Probability of winning a majority of the non-punted categories (independent categories)."""
    active = [p for c, p in category_probs.items() if c not in punt_categories]
    if not active:
        return 0.5
    n = len(active)
    need = n // 2 + 1
    dp = [1.0] + [0.0] * n
    for p in active:
        nxt = [0.0] * (n + 1)
        for wins in range(n):
            if dp[wins] == 0.0:
                continue
            nxt[wins] += dp[wins] * (1.0 - p)
            nxt[wins + 1] += dp[wins] * p
        dp = nxt
    return sum(dp[need:])


def build_category_view(my_totals: Mapping[str, float], opp_totals: Mapping[str, float], punt_categories: list[str],
                        params: ModelParams = DEFAULT_PARAMS, remaining_share: float = 1.0) -> dict[str, dict[str, Any]]:
    view: dict[str, dict[str, Any]] = {}
    for cat in ALL_CATEGORIES:
        my_val = float(my_totals.get(cat, 0.0))
        opp_val = float(opp_totals.get(cat, 0.0))
        prob = category_win_prob(my_val, opp_val, cat, params, remaining_share)
        if cat in REVERSE_CATEGORIES:
            winner = "YOU" if my_val < opp_val else "OPP" if opp_val < my_val else "TIE"
            margin = opp_val - my_val
        else:
            winner = "YOU" if my_val > opp_val else "OPP" if opp_val > my_val else "TIE"
            margin = my_val - opp_val
        if cat in punt_categories:
            winner = "PUNT"
        view[cat] = {"my_value": my_val, "opp_value": opp_val, "margin": margin,
                     "prob_you_win": prob, "winner": winner, "is_punt": cat in punt_categories}
    return view


def view_probs(view: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    return {cat: view[cat]["prob_you_win"] for cat in ALL_CATEGORIES}


def summarize_category_changes(baseline: Mapping[str, Mapping[str, Any]], scenario: Mapping[str, Mapping[str, Any]]):
    deltas: list[dict[str, Any]] = []
    flips: list[str] = []
    for cat in ALL_CATEGORIES:
        b, s = baseline[cat], scenario[cat]
        deltas.append({
            "category": cat,
            "baseline_prob": b["prob_you_win"],
            "new_prob": s["prob_you_win"],
            "delta_prob": s["prob_you_win"] - b["prob_you_win"],
            "my_before": b["my_value"],
            "my_after": s["my_value"],
            "opp_value": b["opp_value"],
        })
        if b["prob_you_win"] < 0.5 <= s["prob_you_win"]:
            flips.append(cat)
    deltas.sort(key=lambda x: x["delta_prob"], reverse=True)
    return deltas, flips


def recommendation_reason(delta_rows: list[dict[str, Any]], flips: list[str]) -> str:
    improved = [row["category"] for row in delta_rows if row["delta_prob"] > 0.02][:2]
    if flips:
        return f"Flips {' + '.join(flips[:2])}; strongest gains in {' + '.join(improved or flips[:2])}."
    if improved:
        return f"Largest category-odds gains: {' + '.join(improved)}."
    return "Improves overall distribution of category outcomes."


def deterministic_score(view: Mapping[str, Mapping[str, Any]], punt_categories: list[str], by_probability: bool = False) -> dict[str, int]:
    wins = losses = ties = 0
    for cat in ALL_CATEGORIES:
        if cat in punt_categories:
            continue
        if by_probability:
            if view[cat]["prob_you_win"] >= 0.5:
                wins += 1
            else:
                losses += 1
        else:
            w = view[cat]["winner"]
            wins += w == "YOU"
            losses += w == "OPP"
            ties += w == "TIE"
    out = {"wins": wins, "losses": losses}
    if not by_probability:
        out["ties"] = ties
    return out
