"""The ported model must reproduce the original waiver optimizer's numbers.

The original module (`waiver_optimizer_api.py`) is kept in git history; while it
is still in the tree these tests compare the pure functions directly.
"""
import importlib.util
import math
from pathlib import Path

import pytest

from fbb import matchup, optimizer, projection
from fbb.config import DEFAULT_PARAMS

OLD_PATH = Path(__file__).resolve().parents[1] / "waiver_optimizer_api.py"
pytestmark = pytest.mark.skipif(not OLD_PATH.exists(), reason="original module removed; parity established before removal")


@pytest.fixture(scope="module")
def old():
    spec = importlib.util.spec_from_file_location("old_waiver", OLD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CASES = [
    ("FG%", 0.481, 0.470), ("FT%", 0.80, 0.85), ("PTS", 650.0, 629.2), ("REB", 259.0, 221.8),
    ("TO", 74.8, 75.1), ("BLK", 27.5, 32.9), ("ST", 41.3, 29.4), ("3PTM", 63.3, 71.3), ("AST", 0.0, 0.0),
]


def test_category_win_prob_matches(old):
    for cat, a, b in CASES:
        assert matchup.category_win_prob(a, b, cat) == pytest.approx(old.category_win_prob(a, b, cat))


def test_matchup_win_probability_matches(old):
    probs = {"FG%": 0.56, "FT%": 0.52, "3PTM": 0.47, "PTS": 0.52, "REB": 0.54, "AST": 0.46, "ST": 0.58, "BLK": 0.46, "TO": 0.50}
    assert matchup.matchup_win_probability(probs, []) == pytest.approx(old.matchup_win_probability(probs, []))
    assert matchup.matchup_win_probability(probs, ["TO"]) == pytest.approx(old.matchup_win_probability(probs, ["TO"]))


PLAYER = {"player_key": "466.p.1", "player_id": 1, "name": "Test Player", "position": "PF", "nba_team": "LAL", "status": "DTD",
          "status_full": "Day-To-Day", "news": "questionable", "return_hint": "", "percent_owned": 55,
          "stats": {"FG%": 0.481, "FT%": 0.751, "3PTM": 1.6, "PTS": 11.1, "REB": 5.3, "AST": 1.8, "ST": 0.8, "BLK": 0.5, "TO": 0.9}}
COUNTS = {"LAL": 3, "PHX": 4}


def test_project_player_contribution_matches(old):
    new = projection.project_player(PLAYER, "average_season", COUNTS, 2)
    ref = old.project_player_contribution(PLAYER, "average_season", COUNTS, 2)
    for key in ("PTS", "REB", "AST", "ST", "BLK", "3PTM", "TO", "FGM", "FGA", "FTM", "FTA", "games_remaining"):
        assert new[key] == pytest.approx(ref[key]), key


def test_project_team_and_totals_match(old):
    players = [PLAYER, {**PLAYER, "player_key": "466.p.2", "nba_team": "PHX", "status": "", "news": ""}]
    new_t, new_c = projection.project_team(players, "average_season", COUNTS, 2)
    old_t, old_c = old.project_team_totals(players, "average_season", COUNTS, 2)
    for key in old_t:
        assert new_t[key] == pytest.approx(old_t[key]), key
    banked = {"FG%": 0.512, "FT%": 0.877, "3PTM": 31, "PTS": 314, "REB": 110, "AST": 45, "ST": 16, "BLK": 9, "TO": 33}
    assert projection.categories_to_totals(banked) == pytest.approx(old.categories_to_totals(banked))
    combined_new = projection.combine_totals(projection.categories_to_totals(banked), new_t)
    combined_old = old.combine_totals(old.categories_to_totals(banked), old_t)
    for key in combined_old:
        assert combined_new[key] == pytest.approx(combined_old[key]), key


def test_blend_recent_matches_when_both_windows_have_percentages(old):
    week = {"nba_team": "LAL", "stats": {"FG%": 0.5, "FT%": 0.8, "PTS": 40, "REB": 20, "AST": 6, "ST": 3, "BLK": 2, "3PTM": 5, "TO": 4}}
    month = {"nba_team": "LAL", "stats": {"FG%": 0.46, "FT%": 0.75, "PTS": 150, "REB": 70, "AST": 20, "ST": 10, "BLK": 7, "3PTM": 18, "TO": 15}}
    new = projection.blend_recent(week, month, {"LAL": 3}, {"LAL": 13}, 3, 12)
    ref = old.recent_2w_per_game_stats(week, month, {"LAL": 3}, {"LAL": 13}, 3, 12)
    for key in ref:
        assert new[key] == pytest.approx(ref[key]), key
    # Only one window present.
    new1 = projection.blend_recent(None, month, {}, {"LAL": 13}, 3, 12)
    ref1 = old.recent_2w_per_game_stats(None, month, {}, {"LAL": 13}, 3, 12)
    for key in ref1:
        assert new1[key] == pytest.approx(ref1[key]), key


def test_drop_value_score_matches(old):
    c = projection.project_player(PLAYER, "average_season", COUNTS, 2)
    for guard, thr, undrop in ((True, 80, True), (False, 80, True), (True, 50, False)):
        assert optimizer.drop_value_score(c, include_percent_owned_guard=guard, ownership_protect_threshold=thr, respect_undroppable=undrop) == pytest.approx(
            old.drop_value_score(c, include_percent_owned_guard=guard, ownership_protect_threshold=thr, respect_undroppable=undrop))
    out = {**c, "status": "O", "percent_owned": 95, "is_undroppable": True}
    assert optimizer.drop_value_score(out) == pytest.approx(old.drop_value_score(out))


def test_build_category_view_and_summary_match(old):
    my = {"FG%": 0.505, "FT%": 0.817, "3PTM": 63.3, "PTS": 689.9, "REB": 259.0, "AST": 133.2, "ST": 41.3, "BLK": 27.5, "TO": 74.8}
    opp = {"FG%": 0.480, "FT%": 0.809, "3PTM": 71.3, "PTS": 629.2, "REB": 221.8, "AST": 156.8, "ST": 29.4, "BLK": 32.9, "TO": 75.1}
    new_v, old_v = matchup.build_category_view(my, opp, ["TO"]), old.build_category_view(my, opp, ["TO"])
    for cat in old_v:
        for k in old_v[cat]:
            assert new_v[cat][k] == pytest.approx(old_v[cat][k]) if isinstance(old_v[cat][k], float) else new_v[cat][k] == old_v[cat][k]
    my2 = {**my, "BLK": 34.0, "AST": 160.0}
    s_new, s_old = matchup.build_category_view(my2, opp, []), old.build_category_view(my2, opp, [])
    d_new, f_new = matchup.summarize_category_changes(new_v, s_new)
    d_old, f_old = old.summarize_category_changes(old_v, s_old)
    assert f_new == f_old == ["BLK", "AST"] or set(f_new) == set(f_old)
    assert [d["category"] for d in d_new] == [d["category"] for d in d_old]
    assert matchup.recommendation_reason(d_new, f_new) == old.recommendation_reason(d_old, f_old)
