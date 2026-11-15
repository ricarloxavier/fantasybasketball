import json

import pytest

from fbb.config import Settings
from fbb.yahoo import client as yc

TOKENS = {"consumer_key": "ck", "consumer_secret": "cs", "access_token": "at", "refresh_token": "rt"}


class FakeOAuth2:
    def __init__(self, *_a, from_file=None, **_k):
        self.from_file = from_file
        self.session = type("S", (), {})()

    def token_is_valid(self):
        return True


@pytest.fixture
def fake_oauth(monkeypatch):
    monkeypatch.setattr(yc, "OAuth2", FakeOAuth2)


def test_seed_json_creates_missing_oauth_file(tmp_path, fake_oauth):
    path = tmp_path / "sub" / "oauth2.json"
    oauth = yc.load_oauth(path, seed_json=json.dumps(TOKENS))
    assert oauth.from_file == str(path)
    assert json.loads(path.read_text()) == TOKENS


def test_existing_oauth_file_wins_over_seed(tmp_path, fake_oauth):
    path = tmp_path / "oauth2.json"
    on_disk = dict(TOKENS, access_token="newer")
    path.write_text(json.dumps(on_disk))
    yc.load_oauth(path, seed_json=json.dumps(TOKENS))
    assert json.loads(path.read_text()) == on_disk


def test_bad_seed_json_is_an_auth_error(tmp_path, fake_oauth):
    with pytest.raises(yc.YahooAuthError, match="YAHOO_OAUTH_JSON"):
        yc.load_oauth(tmp_path / "oauth2.json", seed_json="{not json")
    with pytest.raises(yc.YahooAuthError, match="consumer_key"):
        yc.load_oauth(tmp_path / "oauth2.json", seed_json=json.dumps({"access_token": "x"}))


def test_missing_file_without_seed_still_errors(tmp_path, fake_oauth):
    with pytest.raises(yc.YahooAuthError, match="not found"):
        yc.load_oauth(tmp_path / "oauth2.json")


def test_settings_read_yahoo_oauth_json():
    s = Settings.from_env({"YAHOO_OAUTH_JSON": ' {"a": 1} '})
    assert s.yahoo_oauth_json == '{"a": 1}'
    assert Settings.from_env({}).yahoo_oauth_json == ""
