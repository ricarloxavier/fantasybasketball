import base64

import pytest
from werkzeug.security import generate_password_hash

from fbb.config import Settings
from fbb.yahoo.client import YahooAuthError
from web.app import create_app


@pytest.fixture
def app(tmp_path):
    settings = Settings(web_auth_user="chantasy", web_auth_password_hash=generate_password_hash("pw"), cache_dir=tmp_path / ".cache")
    app = create_app(settings)
    app.config["TESTING"] = True
    return app


def auth_header(user="chantasy", pw="pw"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_requires_login(app):
    c = app.test_client()
    assert c.get("/").status_code == 401
    assert c.get("/api/awards").status_code == 401
    assert c.get("/", headers=auth_header(pw="wrong")).status_code == 401


def test_pages_render(app):
    c = app.test_client()
    r = c.get("/", headers=auth_header())
    assert r.status_code == 200 and b"Putilla Awards" in r.data and b"/static/js/awards.js" in r.data
    r = c.get("/waiver", headers=auth_header())
    assert r.status_code == 200 and b"waiver.js" in r.data
    assert c.get("/static/css/app.css").status_code == 200


def test_unconfigured_auth_denies_everything(tmp_path):
    app = create_app(Settings(cache_dir=tmp_path))
    assert app.test_client().get("/", headers=auth_header()).status_code == 401


def test_yahoo_auth_error_is_json_503_without_traceback(app, monkeypatch):
    import web.routes.awards as awards_routes

    def boom():
        raise YahooAuthError("app not authorised")
    monkeypatch.setattr(awards_routes, "_client", boom)
    r = app.test_client().get("/api/awards", headers=auth_header())
    assert r.status_code == 503
    body = r.get_json()
    assert body["success"] is False and "not authorised" in body["error"] and "traceback" not in body


def test_unexpected_error_hides_details(app, monkeypatch):
    import web.routes.waiver as waiver_routes
    monkeypatch.setattr(waiver_routes, "_client", lambda: object())
    monkeypatch.setattr(waiver_routes, "get_waiver_team_options", lambda *a, **k: 1 / 0)
    r = app.test_client().get("/api/waiver/teams", headers=auth_header())
    assert r.status_code == 500 and r.get_json()["error"] == "Internal error; see server log."


def test_preferences_roundtrip_and_bad_payload(app):
    c = app.test_client()
    assert c.get("/api/waiver/preferences", headers=auth_header()).get_json()["data"]["max_results"] == 20
    r = c.post("/api/waiver/preferences", json={"max_results": 30, "punt_categories": ["TO"]}, headers=auth_header())
    assert r.get_json()["data"]["max_results"] == 30
    assert c.get("/api/waiver/preferences", headers=auth_header()).get_json()["data"]["punt_categories"] == ["TO"]
    r = c.post("/api/waiver/preferences", data="not json", headers={**auth_header(), "Content-Type": "text/plain"})
    assert r.status_code == 400
    # Empty JSON object leaves saved prefs untouched.
    c.post("/api/waiver/preferences", json={}, headers=auth_header())
    assert c.get("/api/waiver/preferences", headers=auth_header()).get_json()["data"]["max_results"] == 30


def test_analyze_uses_query_overrides(app, monkeypatch):
    import web.routes.waiver as waiver_routes
    captured = {}

    def fake(settings, options, team_key="", client=None, params=None):
        captured["options"] = options
        captured["team_key"] = team_key
        return {"success": True, "data": {}}
    monkeypatch.setattr(waiver_routes, "get_waiver_recommendations", fake)
    monkeypatch.setattr(waiver_routes, "_client", lambda: None)
    r = app.test_client().get("/api/waiver/analyze?punt=TO&drop_player=Sandro%20Mamukelashvili&team_key=478.l.17275.t.3&max_results=7", headers=auth_header())
    assert r.status_code == 200
    assert captured["options"].punt_categories == ["TO"] and captured["options"].forced_drop_players == ["Sandro Mamukelashvili"]
    assert captured["options"].max_results == 7 and captured["team_key"] == "478.l.17275.t.3"
