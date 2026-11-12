"""Deliberate differences from the original model, each tested on its own."""
import pytest

from fbb import matchup, projection
from fbb.config import ModelParams


def test_remaining_games_scaling_sharpens_small_leads_late_in_week():
    scaled = ModelParams(remaining_games_scaling=1.0)
    early = matchup.category_win_prob(28.0, 27.0, "BLK", scaled, remaining_share=1.0)
    late = matchup.category_win_prob(28.0, 27.0, "BLK", scaled, remaining_share=0.0)
    unscaled = matchup.category_win_prob(28.0, 27.0, "BLK", ModelParams(), remaining_share=0.0)
    assert early == pytest.approx(unscaled)        # whole week left: identical to the original
    assert late > early                             # one block up with nothing left is nearly certain
    assert late > 0.9


def test_blend_ignores_zero_percentages_from_empty_windows():
    week = {"nba_team": "LAL", "stats": {"FG%": 0.0, "FT%": 0.0, "PTS": 0, "REB": 0, "AST": 0, "ST": 0, "BLK": 0, "3PTM": 0, "TO": 0}}
    month = {"nba_team": "LAL", "stats": {"FG%": 0.46, "FT%": 0.75, "PTS": 150, "REB": 70, "AST": 20, "ST": 10, "BLK": 7, "3PTM": 18, "TO": 15}}
    out = projection.blend_recent(week, month, {"LAL": 3}, {"LAL": 13}, 3, 12)
    assert out["FG%"] == pytest.approx(0.46)
    assert out["FT%"] == pytest.approx(0.75)


def test_real_shot_volume_is_used_when_present():
    per_game = {"FG%": 0.5, "FT%": 0.8, "PTS": 20, "FGM": 8, "FGA": 16, "FTM": 4, "FTA": 5}
    vol = projection.estimate_volume(per_game)
    assert vol == {"FGM": 8.0, "FGA": 16.0, "FTM": 4.0, "FTA": 5.0}
    est = projection.estimate_volume({"FG%": 0.5, "FT%": 0.8, "PTS": 20})
    assert est["FGA"] == pytest.approx(22.0) and est["FTA"] == pytest.approx(4.5)


def test_totals_shift_equals_full_reprojection():
    counts = {"LAL": 3, "PHX": 4}
    mk = lambda key, team, pts: {"player_key": key, "player_id": 1, "name": key, "nba_team": team, "status": "",
                                 "stats": {"FG%": 0.48, "FT%": 0.78, "3PTM": 1, "PTS": pts, "REB": 5, "AST": 2, "ST": 1, "BLK": 0.5, "TO": 1}}
    roster = [mk("a", "LAL", 10), mk("b", "PHX", 20), mk("c", "LAL", 15)]
    add = mk("d", "PHX", 12)
    base, contribs = projection.project_team(roster, "average_season", counts, 2)
    drop = contribs[2]
    shifted = projection.totals_shift(base, remove=drop, add=projection.project_player(add, "average_season", counts, 2))
    full, _ = projection.project_team(roster[:2] + [add], "average_season", counts, 2)
    for key in full:
        assert shifted[key] == pytest.approx(full[key]), key
