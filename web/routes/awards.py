from flask import Blueprint, current_app, jsonify, render_template

from fbb.awards import compute_awards
from fbb.yahoo.client import YahooClient

from ..app import auth

bp = Blueprint("awards", __name__)


def _client() -> YahooClient:
    return YahooClient.from_settings(current_app.config["SETTINGS"], current_app.config["PARAMS"])


@bp.get("/")
@auth.login_required
def index():
    return render_template("awards.html", active="awards")


@bp.get("/api/awards")
@auth.login_required
def api_awards():
    settings = current_app.config["SETTINGS"]
    return jsonify({"success": True, "data": compute_awards(_client(), settings)})


@bp.get("/api/awards/refresh")
@auth.login_required
def api_awards_refresh():
    settings = current_app.config["SETTINGS"]
    return jsonify({"success": True, "data": compute_awards(_client(), settings, use_cache=False)})
