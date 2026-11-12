from fbb.yahoo.parse import extract_category_stats, parse_league_meta, parse_player_node, parse_players_response, parse_team_roster_response, parse_teams_response


def _node(stats=None, extra=None):
    meta = [
        {"player_key": "478.p.6596"}, {"player_id": "6596"}, {"name": {"full": "Sandro Mamukelashvili", "first": "Sandro"}},
        {"editorial_team_abbr": "LAL"}, {"display_position": "PF,C"}, {"status": "DTD"}, {"status_full": "Day-To-Day"},
        {"injury_note": "Ankle"}, {"eligible_positions": [{"position": "PF"}, {"position": "C"}, {"position": "Util"}]},
    ]
    node = [meta]
    if stats is not None:
        node.append({"player_stats": {"0": {"coverage_type": "season"}, "stats": [{"stat": {"stat_id": k, "value": v}} for k, v in stats]}})
    if extra:
        node.append(extra)
    return node


def test_parse_player_node_maps_categories_and_composites():
    p = parse_player_node(_node([("5", ".481"), ("8", "-"), ("10", "1.6"), ("12", "11.1"), ("15", "5.3"), ("16", "1.8"),
                                 ("17", "0.8"), ("18", "0.5"), ("19", "0.9"), ("9004003", "4.5/9.4"), ("9007006", "1.5/2.0")]))
    assert p["player_key"] == "478.p.6596" and p["player_id"] == 6596
    assert p["name"] == "Sandro Mamukelashvili" and p["nba_team"] == "LAL" and p["position"] == "PF,C"
    assert p["status"] == "DTD" and p["status_full"] == "Day-To-Day" and p["injury_note"] == "Ankle"
    assert p["eligible_positions"] == ["PF", "C", "Util"]
    s = p["stats"]
    assert s["FG%"] == 0.481 and s["FT%"] == 0.0 and s["3PTM"] == 1.6 and s["ST"] == 0.8 and s["TO"] == 0.9
    assert s["FGM"] == 4.5 and s["FGA"] == 9.4 and s["FTM"] == 1.5 and s["FTA"] == 2.0


def test_parse_players_response_and_percent_owned():
    data = {"fantasy_content": {"league": [{"league_key": "478.l.17275"}, {"players": {
        "0": {"player": _node([("12", "20")], extra={"percent_owned": [{"coverage_type": "week"}, {"value": "87"}]})},
        "count": 1}}]}}
    rows = parse_players_response(data)
    assert len(rows) == 1 and rows[0]["stats"]["PTS"] == 20 and rows[0]["percent_owned"] == 87.0


def test_parse_team_roster_selected_position():
    data = {"fantasy_content": {"team": [[{"team_key": "478.l.17275.t.3"}], {"roster": {"0": {"players": {
        "0": {"player": _node(extra={"selected_position": [{"coverage_type": "date"}, {"position": "IL"}]})},
        "count": 1}}}}]}}
    rows = parse_team_roster_response(data)
    assert rows[0]["selected_position"] == "IL"


def test_parse_teams_and_league_meta():
    data = {"fantasy_content": {"league": [
        {"league_key": "478.l.17275", "league_id": "17275", "name": "Chantasy", "current_week": "4", "start_week": "1", "end_week": "23",
         "start_date": "2026-10-20", "end_date": "2027-04-11", "num_teams": "12", "season": "2026"},
        {"teams": {"0": {"team": [[{"team_key": "478.l.17275.t.3"}, {"team_id": "3"}, {"name": "All Stars 2018"}, {"is_owned_by_current_login": 1}]]},
                   "1": {"team": [[{"team_key": "478.l.17275.t.10"}, {"team_id": "10"}, {"name": "The Psychopaths"}]]}, "count": 2}}]}}
    teams = parse_teams_response(data)
    assert teams["478.l.17275.t.3"]["is_mine"] and teams["478.l.17275.t.3"]["name"] == "All Stars 2018"
    assert not teams["478.l.17275.t.10"]["is_mine"]
    meta = parse_league_meta(data)
    assert meta["current_week"] == 4 and meta["end_week"] == 23 and meta["league_id"] == "17275"


def test_extract_category_stats_from_team_stats_payload():
    payload = {"fantasy_content": {"team": [[{"team_key": "x"}], {"team_stats": {"stats": [
        {"stat": {"stat_id": "5", "value": ".512"}}, {"stat": {"stat_id": "12", "value": "314"}}, {"stat": {"stat_id": "9004003", "value": "120/234"}}]}}]}}
    s = extract_category_stats(payload)
    assert s["FG%"] == 0.512 and s["PTS"] == 314 and s["FGM"] == 120 and s["FGA"] == 234
