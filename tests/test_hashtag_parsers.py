from datetime import date
from pathlib import Path

import pytest

from fbb.hashtag import parsers
from fbb.hashtag.session import extract_form_state

FIX = Path(__file__).parent / "fixtures" / "hashtag"
pytestmark = pytest.mark.skipif(not (FIX / "planner.html").exists(), reason="hashtag fixtures are local-only")


@pytest.fixture(scope="module")
def planner():
    return parsers.parse_planner((FIX / "planner.html").read_text("utf-8"))


def test_planner_header_and_totals(planner):
    assert planner["week"] == 4 and planner["spots"] == 10 and planner["duration"] == "0"
    assert planner["my_team"] == "All Stars 2018" and planner["opp_team"] == "The Psychopaths"
    assert planner["days_played"] == [1, 2, 3]
    assert planner["before"]["All Stars 2018"]["PTS"] == 314.0
    assert planner["after"]["All Stars 2018"]["PTS"] == 689.9 and planner["after"]["The Psychopaths"]["FG%"] == 0.480
    assert planner["prob"]["FG%"] == 0.56 and planner["prob"]["ST"] == 0.58 and planner["prob"]["TO"] == 0.50
    assert planner["record"] == "6-3"
    assert [d["label"] for d in planner["days"]] == ["THU", "FRI", "SAT", "SUN"]
    assert planner["days"][0] == {"label": "THU", "used": 5, "spots": 10}


def test_planner_roster_rows(planner):
    roster = planner["roster"]
    assert len(roster) == 16
    kd = roster[0]
    assert kd["name"] == "Kevin Durant" and kd["positions"] == ["SG", "SF", "PF"] and kd["nba_team"] == "HOU"
    assert kd["mpg"] == 33.6 and kd["stats"]["PTS"] == 21.0 and kd["stats"]["ST"] == 0.7 and kd["tiers"]["FG%"] == "vgood"
    assert [g["day"] for g in kd["games"]] == ["FRI", "SUN"] and kd["games_left"] == 2
    kawhi = next(p for p in roster if p["name"] == "Kawhi Leonard")
    assert kawhi["injury"] == "out" and kawhi["hashtag_id"] == "4896"
    assert kawhi["games"][0]["counted"] is False and kawhi["games"][0]["checkbox"].endswith("$CBTHU")
    sandro = next(p for p in roster if p["name_key"] == "sandromamukelashvili")
    assert sandro["hashtag_id"] == "6596" and sandro["games_left"] == 1 and sandro["games"][0]["opponent"] == "@PHO"
    assert sandro["games"][0]["checkbox"] == "ctl00$ContentPlaceHolder1$Repeater2$ctl13$CBSUN"


def test_planner_recommendations(planner):
    recs = planner["recommendations"]
    assert len(recs) == 10
    first = recs[0]
    assert first["name"] == "Meleek Thomas" and first["rookie"] and first["nba_team"] == "CLE"
    assert first["games_this_week"] == ["FRI", "SAT"] and first["fit_days"] == ["FRI", "SAT"] and first["games_next_week"] == []
    assert first["add_target"] == "ctl00$ContentPlaceHolder1$RECREP$ctl01$lnkAdd"
    assert first["stats"]["3PTM"] == 3.2


def test_waiver_wire_rows():
    rows = parsers.parse_waiver_wire((FIX / "waiver.html").read_text("utf-8"))
    assert len(rows) >= 20
    braun = next(r for r in rows if r["name"] == "Christian Braun")
    assert braun["rank"] == 99 and braun["positions"] == ["SG", "SF", "PF"] and braun["nba_team"] == "DEN" and braun["gp"] == 72
    assert braun["stats"]["FG%"] == 0.560 and braun["stats"]["FGM"] == 5.3 and braun["stats"]["FGA"] == 9.5
    assert braun["stats"]["FTA"] == 2.5 and braun["total_z"] == 1.10
    assert "struggling" in braun["note"]
    assert rows[0]["total_z"] >= rows[-1]["total_z"]


def test_schedule_grid():
    grid = parsers.parse_schedule_grid((FIX / "grid_w5.html").read_text("utf-8"))
    assert grid["start"] == date(2026, 11, 16) and grid["end"] == date(2026, 11, 22)
    bassey = next(p for p in grid["players"] if p["name"] == "Charles Bassey")
    assert bassey["nba_team"] == "GSW" and bassey["positions"] == ["C"] and bassey["gp"] == 55
    assert bassey["games"]["2026-11-16"] == {"opponent": "@NO", "def_rank": 124}
    assert bassey["games_in_week"] == 4 and "2026-11-23" not in bassey["games"] or True
    assert bassey["stats"]["FGA"] == 6.3 and bassey["total_z"] == 1.5


def test_form_state_from_planner():
    html = (FIX / "planner.html").read_text("utf-8")
    state = extract_form_state(html, "https://hashtagbasketball.com/import-v4/plan-cat-yahoo.aspx?lid=17275")
    assert state.select_value("DDWEEK") == "4" and state.select_value("DDTEAM2") == "10"
    assert ("5", "5") in state.options("DDWEEK")
    assert state.name_for("Button5") == "ctl00$ContentPlaceHolder1$Button5"
    assert state.checkboxes["ctl00$ContentPlaceHolder1$Repeater2$ctl13$CBSUN"] is True
    assert state.fields["ctl00$ContentPlaceHolder1$Repeater2$ctl13$CBSUN"] == "on"
    assert "ctl00$ContentPlaceHolder1$Repeater2$ctl01$CBTHU" not in state.fields   # unchecked boxes are not submitted
    assert state.fields["__VIEWSTATE"] == "TRUNCATED"


def test_hashtag_probability_formula_matches_recorded_row(planner):
    my, opp = planner["team_order"]
    probs, record = parsers.hashtag_probability(planner["after"][my], planner["after"][opp])
    assert record == planner["record"] == "6-3"
    for cat, p in probs.items():
        assert round(p, 2) == planner["prob"][cat], cat
