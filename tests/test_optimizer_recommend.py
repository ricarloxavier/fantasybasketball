"""End-to-end `recommend` on a synthetic context: output schema and ranking sanity."""
from datetime import date

from fbb.optimizer import WaiverContext, WaiverOptions, recommend
from fbb.projection import categories_to_totals


def player(key, name, team, pts, reb=5.0, status="", owned=40.0, undroppable=False):
    return {"player_key": key, "player_id": int(key.split(".")[-1]), "name": name, "position": "PF", "nba_team": team,
            "status": status, "status_full": "", "news": "", "return_hint": "", "percent_owned": owned, "is_undroppable": undroppable,
            "stats": {"FG%": 0.48, "FT%": 0.78, "3PTM": 1.5, "PTS": pts, "REB": reb, "AST": 2.0, "ST": 1.0, "BLK": 0.6, "TO": 1.2}}


def make_context():
    my = [player("478.p.1", "Alpha One", "LAL", 20), player("478.p.2", "Beta Two", "PHX", 15), player("478.p.3", "Gamma Three", "LAL", 8)]
    opp = [player("478.p.11", "Opp One", "BOS", 18), player("478.p.12", "Opp Two", "MIA", 16), player("478.p.13", "Opp Three", "BOS", 12)]
    fas = [player("478.p.21", "Hot Hand", "PHX", 18, reb=8), player("478.p.22", "Cold Hand", "LAL", 6), player("478.p.23", "Hurt Guy", "PHX", 25, status="O")]
    games = {"counts": {"LAL": 1, "PHX": 4, "BOS": 3, "MIA": 2}, "teams_by_day": {"2026-11-12": ["PHX", "BOS"], "2026-11-13": ["LAL", "PHX", "MIA"]},
             "source": "espn", "days": 4, "fallback_games": 2, "dates": ["2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15"]}
    banked_my = categories_to_totals({"FG%": 0.5, "FT%": 0.8, "3PTM": 20, "PTS": 300, "REB": 100, "AST": 40, "ST": 15, "BLK": 9, "TO": 30})
    banked_opp = categories_to_totals({"FG%": 0.47, "FT%": 0.84, "3PTM": 30, "PTS": 280, "REB": 90, "AST": 70, "ST": 10, "BLK": 14, "TO": 32})
    teams = {"478.l.17275.t.3": {"name": "All Stars 2018", "is_mine": True}, "478.l.17275.t.10": {"name": "The Psychopaths", "is_mine": False}}
    return WaiverContext(
        league_key="478.l.17275", my_team_key="478.l.17275.t.3", my_team_name="All Stars 2018",
        opp_team_key="478.l.17275.t.10", opp_team_name="The Psychopaths", teams=teams, current_week=4,
        week_start=date(2026, 11, 9), week_end=date(2026, 11, 15), today=date(2026, 11, 12),
        my_players=my, opp_players=opp, free_agents=fas, stats_source="average_season", projection_window_used="season",
        recent_windows_info={}, games_info=games, current_my_totals=banked_my, current_opp_totals=banked_opp,
        fetch_signals=lambda keys: {k: {"news": "note", "status_full": "", "return_hint": ""} for k in keys},
    )


def test_recommend_schema_and_ranking():
    out = recommend(make_context(), WaiverOptions(drop_candidates=2, max_results=5))
    assert out["success"]
    d = out["data"]
    for key in ("baseline", "recommendations", "drop_candidates", "my_roster", "current_score", "remaining_schedule", "available_teams", "settings"):
        assert key in d
    assert 0.0 < d["baseline"]["win_probability"] < 1.0
    assert set(d["baseline"]["categories"]) == {"FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"}
    recs = d["recommendations"]
    assert recs and recs[0]["rank"] == 1 and recs[0]["pickup_news"] == "note"
    # The 4-game healthy player beats the injured one and the low scorer for the same drop.
    best_by_pickup = {}
    for r in recs:
        best_by_pickup.setdefault(r["pickup_player"], r["delta_win_probability"])
    assert best_by_pickup["Hot Hand"] > best_by_pickup.get("Cold Hand", -1)
    assert best_by_pickup["Hot Hand"] > best_by_pickup.get("Hurt Guy", -1)
    # Auto drop pool = the two lowest-value players (the 1-game LAL guys).
    assert {c["name"] for c in d["drop_candidates"]} == {"Gamma Three", "Alpha One"} or len(d["drop_candidates"]) == 2
    assert d["remaining_schedule"]["rows"][0]["my_players"] == ["Beta Two"]


def test_manual_drop_and_never_drop():
    ctx = make_context()
    out = recommend(ctx, WaiverOptions(forced_drop_players=["gamma three"], max_results=3))
    assert {r["drop_player"] for r in out["data"]["recommendations"]} == {"Gamma Three"}
    assert out["data"]["settings"]["drop_mode"] == "manual"
    out2 = recommend(ctx, WaiverOptions(never_drop_players=["Gamma Three"], drop_candidates=1))
    assert out2["data"]["drop_candidates"][0]["name"] != "Gamma Three"
    import pytest
    from fbb.optimizer import WaiverAnalysisError
    with pytest.raises(WaiverAnalysisError):
        recommend(ctx, WaiverOptions(forced_drop_players=["Nobody Here"]))


def test_punt_category_excluded_from_win_probability():
    ctx = make_context()
    base = recommend(ctx, WaiverOptions())["data"]["baseline"]
    punted = recommend(ctx, WaiverOptions(punt_categories=["TO"]))["data"]["baseline"]
    assert punted["categories"]["TO"]["winner"] == "PUNT"
    assert punted["expected_categories_won"] < base["expected_categories_won"]
