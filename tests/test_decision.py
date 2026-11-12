from datetime import date
from pathlib import Path

import pytest

from fbb.hashtag import parsers
from fbb.hashtag.decision import DecisionContext, DecisionWeights, addable_days, evaluate_drop, per_game_value_table, player_z

FIX = Path(__file__).parent / "fixtures" / "hashtag"
pytestmark = pytest.mark.skipif(not (FIX / "planner.html").exists(), reason="hashtag fixtures are local-only")


@pytest.fixture(scope="module")
def ctx():
    planner = parsers.parse_planner((FIX / "planner.html").read_text("utf-8"))
    fas = parsers.parse_waiver_wire((FIX / "waiver.html").read_text("utf-8"))
    remaining = {"2026-11-12": ["PHI", "TOR", "DET", "WSH", "CHA", "PHX", "MIN", "DAL"],
                 "2026-11-13": ["UTA", "HOU", "ATL", "CHI", "MEM", "NOP", "SAS", "GSW", "LAC", "OKC", "DEN", "CLE", "SAC", "IND"],
                 "2026-11-14": ["DEN", "NOP", "CHI", "MIN", "MEM", "BKN", "MIA", "BOS"],
                 "2026-11-15": ["WSH", "HOU", "NYK", "TOR", "LAC", "CHA", "PHX", "LAL", "SAC", "OKC", "IND", "PHI", "UTA", "SAS", "GSW", "CLE"]}
    return DecisionContext(planner=planner, free_agents=fas, today=date(2026, 11, 12), week_start=date(2026, 11, 9),
                           next_week_games={"LAL": 3, "GSW": 4, "DEN": 4, "HOU": 4, "IND": 4, "UTA": 3, "POR": 4, "LAC": 4, "SAC": 3},
                           remaining_teams_by_day=remaining, recent_mpg={"sandromamukelashvili": 28.0, "christianbraun": 25.0},
                           hashtag_probs=planner["prob"])


def test_value_table_and_z(ctx):
    table = per_game_value_table(ctx.free_agents + ctx.planner["roster"])
    assert table["n"] > 30 and 0.4 < table["league_fg"] < 0.55
    kd = next(p for p in ctx.planner["roster"] if p["name"] == "Kevin Durant")
    z_kd, cats = player_z(kd["stats"], table)
    z_low = player_z(ctx.free_agents[-1]["stats"], table)[0]
    assert z_kd > z_low and cats["PTS"] > 0
    assert player_z(kd["stats"], table, punt=["TO"])[0] != z_kd


def test_addable_days_respects_active_spots():
    days = [{"label": "THU", "used": 10, "spots": 10}, {"label": "FRI", "used": 7, "spots": 10}, {"label": "SUN", "used": 9, "spots": 10}]
    assert addable_days(["THU", "FRI", "SUN"], days, set()) == ["FRI", "SUN"]
    assert addable_days(["THU", "FRI"], days, {"THU"}) == ["THU", "FRI"]


def test_evaluate_drop_sandro(ctx):
    rep = evaluate_drop(ctx, "Mamukelashvili", DecisionWeights(), top=6)
    assert rep["drop"]["name"] == "Sandro Mamukelashvili" and rep["drop"]["games_left_this_week"] == 1
    assert rep["drop"]["opportunity"] == pytest.approx(min(28.0 / 23.8, 1.3), abs=0.01)
    m = rep["matchup"]
    assert 0 < m["baseline_win_probability"] < 1 and m["hashtag_record"] == "6-3"
    assert abs(m["hashtag_win_probability"] - 0.5) < 0.3
    cands = rep["candidates"]
    assert len(cands) == 6 and cands[0]["score"] >= cands[-1]["score"]
    for c in cands:
        assert c["games_this_week"] <= 4 and set(c["playable_days"]) <= {"THU", "FRI", "SAT", "SUN"}
        assert "delta_week_pts" in c and "delta_ros_z" in c
    assert rep["verdict"]["action"] in {"DROP", "STREAM", "MARGINAL", "HOLD"}


def test_unknown_and_ambiguous_names(ctx):
    with pytest.raises(ValueError):
        evaluate_drop(ctx, "Nobody")
    with pytest.raises(ValueError):
        evaluate_drop(ctx, "Jalen")      # Jalen Johnson and Jalen Smith


def test_punt_changes_scores(ctx):
    base = evaluate_drop(ctx, "Mamukelashvili", DecisionWeights(), top=3)
    punted = evaluate_drop(ctx, "Mamukelashvili", DecisionWeights(punt=["TO"]), top=3)
    assert base["matchup"]["baseline_win_probability"] != punted["matchup"]["baseline_win_probability"]
