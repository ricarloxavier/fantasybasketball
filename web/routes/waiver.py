from flask import Blueprint, current_app, jsonify, render_template, request

from fbb import prefs as P
from fbb.optimizer import get_waiver_recommendations, get_waiver_team_options
from fbb.yahoo.client import YahooClient

from ..app import api_error, auth

bp = Blueprint("waiver", __name__)


def _client() -> YahooClient:
    return YahooClient.from_settings(current_app.config["SETTINGS"], current_app.config["PARAMS"])


def _user() -> str:
    return auth.current_user() or "default"


@bp.get("/waiver")
@auth.login_required
def waiver_page():
    return render_template("waiver.html", active="waiver")


@bp.get("/api/waiver/analyze")
@auth.login_required
def api_waiver_analyze():
    """Saved preferences overlaid with query-string overrides (punt, drop_player, never_drop, team_key, ...)."""
    store: P.PrefsStore = current_app.config["PREFS"]
    merged = P.apply_query(store.get(_user()), request.args)
    options = P.to_options(merged)
    settings = current_app.config["SETTINGS"]
    result = get_waiver_recommendations(settings, options, team_key=merged["selected_team_key"], client=_client(), params=current_app.config["PARAMS"])
    return jsonify(result)


@bp.get("/api/waiver/teams")
@auth.login_required
def api_waiver_teams():
    store: P.PrefsStore = current_app.config["PREFS"]
    preferred = (request.args.get("team_key") or store.get(_user()).get("selected_team_key") or "").strip()
    return jsonify(get_waiver_team_options(current_app.config["SETTINGS"], preferred, client=_client()))


@bp.route("/api/waiver/preferences", methods=["GET", "POST"])
@auth.login_required
def api_waiver_preferences():
    store: P.PrefsStore = current_app.config["PREFS"]
    if request.method == "GET":
        return jsonify({"success": True, "data": store.get(_user())})
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error(400, "Expected a JSON object of preferences.")
    return jsonify({"success": True, "data": store.update(_user(), payload)})
