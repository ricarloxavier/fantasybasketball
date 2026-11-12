from datetime import datetime
from zoneinfo import ZoneInfo

from fbb.cache import TTLCache
from fbb.schedule import NbaSchedule
from fbb.transactions import add_events, same_day_adds, tally_adds_drops
from tests.test_schedule import FixtureSession


def tx(ts, name, team, kind, fteam):
    data = {"type": kind, "source_type": "freeagents", "destination_team_name": fteam} if kind == "add" else {"type": "drop", "source_team_name": fteam}
    return [{"timestamp": str(ts)}, {"players": {"count": 1, "0": {"player": [[{"name": {"full": name}}, {"editorial_team_abbr": team}], {"transaction_data": data}]}}}]


def ts(y, m, d, h):
    return int(datetime(y, m, d, h, tzinfo=ZoneInfo("America/New_York")).timestamp())


def test_tally_and_same_day_adds():
    txs = [
        tx(ts(2026, 11, 12, 15), "Anfernee Simons", "PHI", "add", "All Stars 2018"),   # PHI plays Nov 12
        tx(ts(2026, 11, 12, 15), "Christian Braun", "DEN", "add", "All Stars 2018"),   # DEN does not
        tx(ts(2026, 11, 12, 16), "Sandro Mamukelashvili", "LAL", "drop", "All Stars 2018"),
        tx(ts(2026, 11, 13, 10), "Christian Braun", "DEN", "add", "Alpha Kings"),      # DEN plays Nov 13? check fixture
    ]
    tallies = tally_adds_drops(txs)
    assert tallies["adds"]["Christian Braun"] == 2 and tallies["drops"]["Sandro Mamukelashvili"] == 1
    sched = NbaSchedule(cache=TTLCache(), session=FixtureSession())
    res = same_day_adds(add_events(txs), sched)
    rows = {(r["player"], r["date"]): r["plays_that_day"] for r in res["rows"]}
    assert rows[("Anfernee Simons", "2026-11-12")] is True
    assert rows[("Christian Braun", "2026-11-12")] is False
    assert res["by_team"]["All Stars 2018"] == {"adds": 2, "same_day": 1, "no_game": 1}
