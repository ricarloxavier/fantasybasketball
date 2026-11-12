"""Flask app factory for the league site: Putilla awards and the waiver optimizer.

- credentials come from the environment (hashed password, constant-time compare)
- every error is JSON with a real status code and no traceback
- debug is off unless FLASK_DEBUG=1; binds to 127.0.0.1 unless HOST is set
"""

from __future__ import annotations

import hmac
import logging
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify
from flask_httpauth import HTTPBasicAuth
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash

from fbb.cache import TTLCache
from fbb.config import DEFAULT_PARAMS, ModelParams, Settings
from fbb.optimizer import WaiverAnalysisError
from fbb.prefs import PrefsStore
from fbb.yahoo.client import YahooApiError, YahooAuthError

log = logging.getLogger(__name__)
auth = HTTPBasicAuth()
HERE = Path(__file__).resolve().parent


@auth.verify_password
def verify_password(username: str, password: str) -> Optional[str]:
    from flask import current_app
    settings: Settings = current_app.config["SETTINGS"]
    if not settings.web_auth_user or not settings.web_auth_password_hash:
        return None  # unconfigured: deny everything, never fall back to a default login
    if hmac.compare_digest(username or "", settings.web_auth_user) and check_password_hash(settings.web_auth_password_hash, password or ""):
        return username
    return None


def api_error(status: int, message: str, **extra):
    payload = {"success": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


def create_app(settings: Optional[Settings] = None, params: ModelParams = DEFAULT_PARAMS) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(HERE / "static"))
    app.config["SETTINGS"] = settings
    app.config["PARAMS"] = params
    app.config["PREFS"] = PrefsStore(settings.cache_dir.parent / "waiver_user_prefs.json")
    app.config["CACHE"] = TTLCache(settings.cache_dir, "web")
    app.config["JSON_SORT_KEYS"] = False
    if not settings.web_auth_user or not settings.web_auth_password_hash:
        log.warning("WEB_AUTH_USER / WEB_AUTH_PASSWORD_HASH not set: every request will be refused.")

    from .routes.awards import bp as awards_bp
    from .routes.waiver import bp as waiver_bp
    app.register_blueprint(awards_bp)
    app.register_blueprint(waiver_bp)

    @app.errorhandler(YahooAuthError)
    def _auth_error(exc):
        log.error("Yahoo auth problem: %s", exc)
        return api_error(503, f"Yahoo authentication problem: {exc}")

    @app.errorhandler(YahooApiError)
    def _yahoo_error(exc):
        log.error("Yahoo API problem: %s", exc)
        return api_error(502, f"Yahoo API problem: {exc}")

    @app.errorhandler(WaiverAnalysisError)
    def _analysis_error(exc):
        return api_error(400, str(exc))

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        if exc.code == 401:
            resp = exc.get_response()
            return resp
        return api_error(exc.code or 500, exc.description or exc.name)

    @app.errorhandler(Exception)
    def _unexpected(exc):
        log.exception("Unhandled error")
        return api_error(500, "Internal error; see server log.")

    return app
