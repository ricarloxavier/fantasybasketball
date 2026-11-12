from fbb.categories import name_key, normalize_category, normalize_name_token, normalize_team, parse_punt_categories


def test_normalize_category_aliases():
    assert normalize_category("3PM") == "3PTM"
    assert normalize_category("stl") == "ST"
    assert normalize_category("TOV") == "TO"
    assert normalize_category(" fg ") == "FG%"
    assert normalize_category("REB") == "REB"


def test_parse_punt_categories_from_string_and_list():
    assert parse_punt_categories("TO, stl,bogus") == ["TO", "ST"]
    assert parse_punt_categories(["3PM", "3PTM"]) == ["3PTM"]
    assert parse_punt_categories(None) == []


def test_team_aliases_cover_yahoo_espn_and_hashtag_codes():
    for raw, expected in {"GS": "GSW", "PHO": "PHX", "NO": "NOP", "NY": "NYK", "SA": "SAS", "WAS": "WSH", "BRK": "BKN", "uta": "UTA"}.items():
        assert normalize_team(raw) == expected


def test_name_keys():
    assert normalize_name_token("De'Andre Hunter") == "deandrehunter"
    assert name_key("Nicolás Claxton") == "nicolasclaxton"
    assert name_key("Jimmy Butler III") == "jimmybutler"
    assert name_key("Jaren Jackson Jr.") == "jarenjackson"
    assert name_key("Sandro Mamukelashvili") == "sandromamukelashvili"
