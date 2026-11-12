from fbb.prefs import DEFAULT_PREFS, PrefsStore, apply_query, sanitize, to_options


def test_sanitize_keeps_base_for_missing_keys():
    base = sanitize({"max_results": 30, "punt_categories": ["TO"]})
    assert base["max_results"] == 30 and base["punt_categories"] == ["TO"]
    again = sanitize({}, base)                      # empty payload changes nothing
    assert again == base
    assert sanitize({"max_results": 999}, base)["max_results"] == 50
    assert sanitize({"projection_window": "weird"}, base)["projection_window"] == "recent_2w"
    assert sanitize({"selected_drop_players": "a, b,,a"}, base)["selected_drop_players"] == ["a", "b"]


def test_query_aliases_override_saved_prefs():
    saved = sanitize({"punt_categories": ["TO"], "drop_player_text": "Someone"})
    merged = apply_query(saved, {"punt": "3PM,STL", "drop_player": "Sandro Mamukelashvili", "team_key": "478.l.17275.t.3"})
    assert merged["punt_categories"] == ["3PTM", "ST"]
    assert merged["selected_drop_players"] == ["Sandro Mamukelashvili"] and merged["drop_player_text"] == ""
    assert merged["selected_team_key"] == "478.l.17275.t.3"
    opts = to_options(merged)
    assert opts.forced_drop_players == ["Sandro Mamukelashvili"] and opts.punt_categories == ["3PTM", "ST"]


def test_store_roundtrip(tmp_path):
    store = PrefsStore(tmp_path / "prefs.json")
    assert store.get("u") == dict(DEFAULT_PREFS)
    store.update("u", {"drop_candidates": 6})
    assert store.get("u")["drop_candidates"] == 6
    store.update("u", {})                             # empty POST keeps everything
    assert store.get("u")["drop_candidates"] == 6
