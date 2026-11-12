import importlib.util
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fbb.awards import data as D
from fbb.awards.engine import calculate_awards

OLD = Path(__file__).resolve().parents[1] / "putilla_awards_v2.py"
TZ = ZoneInfo("America/New_York")


def ts(y, m, d, h=12):
    return int(datetime(y, m, d, h, tzinfo=TZ).timestamp())


def tx(timestamp, moves):
    """moves: list of (name, key, action, team_key, team_name, source)."""
    players = {"count": len(moves)}
    for i, (name, key, action, team_key, team_name, source) in enumerate(moves):
        data = {"type": action}
        if action == "add":
            data.update({"source_type": source, "destination_team_key": team_key, "destination_team_name": team_name})
        else:
            data.update({"source_team_key": team_key, "source_team_name": team_name})
        players[str(i)] = {"player": [[{"player_key": key}, {"name": {"full": name}}, {"editorial_team_abbr": "LAL"}, {"display_position": "PF"}],
                                      {"transaction_data": data}]}
    return [{"transaction_key": "x", "timestamp": str(timestamp)}, {"players": players}]


TRANSACTIONS = [
    tx(ts(2026, 10, 27), [("Putilla Pete", "p.1", "add", "t.1", "Team A", "freeagents")]),
    tx(ts(2026, 11, 3), [("Putilla Pete", "p.1", "drop", "t.1", "Team A", "")]),
    tx(ts(2026, 11, 4), [("Putilla Pete", "p.1", "add", "t.2", "Team B", "waivers"), ("Loyal Lou", "p.2", "add", "t.2", "Team B", "freeagents")]),
    tx(ts(2026, 11, 17), [("Putilla Pete", "p.1", "drop", "t.2", "Team B", "")]),
    tx(ts(2026, 11, 18), [("Putilla Pete", "p.1", "add", "t.1", "Team A", "freeagents")]),
    tx(ts(2026, 11, 10), [("Churn Carl", "p.3", "add", "t.3", "Team C", "waivers")]),
    tx(ts(2026, 11, 12), [("Churn Carl", "p.3", "drop", "t.3", "Team C", "")]),
    tx(ts(2026, 11, 13), [("Churn Carl", "p.3", "add", "t.1", "Team A", "freeagents")]),
    tx(ts(2026, 11, 20), [("Churn Carl", "p.3", "drop", "t.1", "Team A", "")]),
]


def test_timeline_pairs_adds_and_drops_per_team():
    stints, drops = D.build_roster_timeline(TRANSACTIONS)
    assert drops == {"Putilla Pete": 2, "Churn Carl": 2}
    pete = stints["Putilla Pete"]
    assert [(s["team_name"], s["drop_timestamp"] is None) for s in pete] == [("Team A", False), ("Team B", False), ("Team A", True)]
    assert stints["Loyal Lou"][0]["drop_timestamp"] is None


def test_week_mapping_from_league_start():
    origin = D.season_week_origin("2026-10-20")   # Tuesday opener -> Monday Oct 19
    assert origin == date(2026, 10, 19)
    assert D.timestamp_to_week(ts(2026, 10, 27), origin, 23) == 2
    assert D.timestamp_to_week(ts(2026, 11, 4), origin, 23) == 3
    assert D.timestamp_to_week(ts(2027, 9, 1), origin, 23) == 23


def test_player_value_and_bonus():
    assert D.player_value({"PTS": 50}) == (D.DISQUALIFIED, {})
    total, cats = D.player_value({"PTS": 300, "REB": 100, "AST": 60, "ST": 20, "BLK": 15, "3PTM": 30, "TO": 40})
    base = 300 * .03 + 100 * .06 + 60 * .08 + 20 * .35 + 15 * .35 + 30 * .12 - 40 * .10
    assert total == pytest.approx(base * 1.15)
    assert cats["ST"] == pytest.approx(7.0)


def test_analyze_and_awards():
    stints, drops = D.build_roster_timeline(TRANSACTIONS)
    origin = D.season_week_origin("2026-10-20")
    stats = {"p.1": {"PTS": 400, "REB": 150, "AST": 50, "ST": 20, "BLK": 10, "3PTM": 30, "TO": 40, "FG%": 0.48, "FT%": 0.8},
             "p.3": {"PTS": 220, "REB": 60, "AST": 20, "ST": 5, "BLK": 5, "3PTM": 10, "TO": 30, "FG%": 0.42, "FT%": 0.7}}
    putillas = {p: s for p, s in stints.items() if drops.get(p, 0) >= 2}
    values = D.analyze_stints(putillas, stats, current_week=6, week_origin=origin, max_week=23, drop_counts=drops)
    assert values and values[0]["total_value"] >= values[-1]["total_value"]
    assert all(v["stats"]["FG%"] == stats[v["player_key"]]["FG%"] for v in values)
    awards = calculate_awards(values, stints, drops, current_week=6)
    assert awards["total_stints"] == len(values)
    assert awards["team_awards"]["putilla_suprema"]["winner"]["team_name"] in {"Team A", "Team B"}
    assert awards["team_awards"]["el_putilla_housewife"]["winner"] == {"team_name": "Team B", "player_name": "Team Total", "unique_players": 1, "player_list": ["Loyal Lou"]}
    assert set(awards["player_awards"]) == {"la_putilla_escondida", "putilla_MVP", "putilla_basura"}


@pytest.mark.skipif(not OLD.exists(), reason="original module removed")
def test_parity_with_original_timeline_and_value():
    spec = importlib.util.spec_from_file_location("old_putilla", OLD)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    new_stints, new_drops = D.build_roster_timeline(TRANSACTIONS)
    old_stints, old_drops = old.build_roster_timeline(TRANSACTIONS)
    assert dict(new_drops) == dict(old_drops)
    assert {k: [(s["team_key"], s["add_timestamp"], s["drop_timestamp"]) for s in v] for k, v in new_stints.items()} == \
           {k: [(s["team_key"], s["add_timestamp"], s["drop_timestamp"]) for s in v] for k, v in old_stints.items()}
    line = {"PTS": 300, "REB": 100, "AST": 60, "ST": 20, "BLK": 15, "3PTM": 30, "TO": 40}
    assert D.player_value(line)[0] == pytest.approx(old.calculate_player_value(line)[0])
