"""Local preview of the web app with a throwaway login (user `dev`, password `dev`).

For looking at the pages on your own machine only. Production uses the .env login.
Usage: venv/bin/python scripts/dev_server.py [port]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from werkzeug.security import generate_password_hash  # noqa: E402

from fbb.config import Settings  # noqa: E402
from web.app import create_app  # noqa: E402

base = Settings.from_env()
settings = Settings(**{**base.__dict__, "web_auth_user": "dev", "web_auth_password_hash": generate_password_hash("dev"),
                       "host": "127.0.0.1", "port": int(sys.argv[1]) if len(sys.argv) > 1 else 5002})
app = create_app(settings)
print(f"dev login: dev / dev  ->  http://{settings.host}:{settings.port}")
app.run(host=settings.host, port=settings.port, debug=False)
