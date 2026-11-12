# gpt-y-fbb

Fantasy basketball tools for the Chantasy Yahoo league. All logic lives in the `fbb/` package; `web/` is the Flask site; `fbb/cli.py` is the `fbb` command.

- Run tests: `venv/bin/python -m pytest -q`
- CLI: `venv/bin/python -m fbb.cli <command>` (`whoami`, `waiver`, `awards`, `transactions`, `roster ...`, `auth`)
- Web: `scripts/start_webapp.sh` (gunicorn) or `FLASK_DEBUG=1 venv/bin/python -c "from web.app import create_app; create_app().run()"`
- Config: `.env` (never committed; keys listed in `.env.example`). Model constants: `fbb/config.py` `ModelParams`.
- Yahoo access: `oauth2.json` (gitignored). If Yahoo returns 403 "not authorized", run `fbb auth`.
- Hashtag Basketball premium data: `fbb/hashtag/`; answer roster questions with `fbb roster ...` and quote its numbers.
- Add features inside the package and the CLI, not as new top-level scripts. Keep tests green; no `sys.exit`/bare `except` in library code.
