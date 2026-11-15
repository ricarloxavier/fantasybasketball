# Deploying the league site

The site is a Flask app served by gunicorn (`web/wsgi.py`). It needs:

- `oauth2.json` with a linked Yahoo account (run `fbb auth` on your machine first and copy the file).
- A `.env` with at least `WEB_AUTH_USER`, `WEB_AUTH_PASSWORD_HASH`, `FANTASY_LEAGUE_ID`. See `.env.example`.
- Python 3.12+ and `pip install -r requirements.txt`.

## On your own machine (share with the league temporarily)

```bash
scripts/start_webapp.sh
```

Then expose it with a tunnel, for example `ngrok http 5000`, and share the tunnel URL plus the login. The tunnel URL changes every time ngrok restarts unless you have a reserved domain. Stop the tunnel when you are done.

## On a host (Railway, Render, a VPS)

- Start command: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 180 web.wsgi:app`
- Set `HOST=0.0.0.0` only through the start command above; never set `FLASK_DEBUG=1` in production.
- Provide `oauth2.json` as a secret file (or mount it) and point `YAHOO_OAUTH_FILE` at it. The app refreshes the access token in place, so the file must be writable. If the host only offers environment variables, put the file's contents in `YAHOO_OAUTH_JSON` instead; the file is written from it on first use.
- Set the `.env` keys as environment variables.
- Caches live under `FBB_CACHE_DIR` (default `.cache/`); they are safe to delete.

## In a Claude cloud session (phone or web)

Cloud sessions clone the repo fresh, so none of the gitignored secrets exist there. The `fantasybasketball` cloud environment allows the Yahoo, Hashtag Basketball and ESPN hosts and installs the venv on start. Add these as environment variables in that environment's settings (never commit them):

- `YAHOO_OAUTH_JSON`: the whole contents of a linked `oauth2.json` from your machine (`fbb auth` there first).
- `HASHTAG_EMAIL`, `HASHTAG_PASSWORD`: the hashtag+ login.

Then any session on that environment can run `venv/bin/python -m fbb.cli whoami`, `waiver`, `roster ...` and the rest.

## Password

Generate a hash for `WEB_AUTH_PASSWORD_HASH` with:

```bash
venv/bin/python -c "from werkzeug.security import generate_password_hash as g; import getpass; print(g(getpass.getpass('password: ')))"
```
