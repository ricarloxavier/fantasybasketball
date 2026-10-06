#!/usr/bin/env python3
"""Start the production server with a private, writable Yahoo OAuth file."""

import json
import os
import tempfile


def prepare_oauth_file():
    credentials = os.environ.get("YAHOO_OAUTH_JSON")
    if credentials:
        parsed = json.loads(credentials)
        if not isinstance(parsed, dict):
            raise ValueError("YAHOO_OAUTH_JSON must contain a JSON object")
        oauth_dir = tempfile.mkdtemp(prefix="yahoo-oauth-")
        oauth_path = os.path.join(oauth_dir, "oauth2.json")
        with open(oauth_path, "w", encoding="utf-8") as oauth_file:
            json.dump(parsed, oauth_file)
        os.chmod(oauth_path, 0o600)
        os.environ["YAHOO_OAUTH_FILE"] = oauth_path
    elif not os.path.isfile(os.environ.get("YAHOO_OAUTH_FILE", "oauth2.json")):
        raise RuntimeError("Set YAHOO_OAUTH_JSON or provide YAHOO_OAUTH_FILE")


if __name__ == "__main__":
    if not os.environ.get("APP_USERNAME") or not os.environ.get("APP_PASSWORD"):
        raise RuntimeError("Set APP_USERNAME and APP_PASSWORD before starting the public app")
    prepare_oauth_file()
    port = int(os.environ.get("PORT", "10000"))
    os.execvp(
        "gunicorn",
        ["gunicorn", "--bind", f"0.0.0.0:{port}", "--workers", "1", "--timeout", "120", "web_app:app"],
    )
