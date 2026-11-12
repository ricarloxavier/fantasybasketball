# gpt-y-fbb

Tools for the Chantasy Yahoo fantasy basketball league:

- **Waiver optimizer** (`/waiver`, `fbb waiver`): ranks add/drop moves for the current head-to-head matchup by the change in win probability, from projected category totals (banked score + remaining games).
- **Roster decisions** (`fbb roster`): drop/add questions answered with Hashtag Basketball premium data (projections, schedule, injuries) across this week, next week and the rest of the season.
- **Putilla awards** (`/`, `fbb awards`): league honours for the most-dropped players and the teams that kept picking them up.
- **Transactions** (`fbb transactions`): most added and dropped players.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env     # then fill in the values
```

**Yahoo.** Create an app at https://developer.yahoo.com/apps (type Web, redirect `oob`, Fantasy Sports read permission). Put the client id and secret in `oauth2.json`:

```json
{"consumer_key": "YOUR_CLIENT_ID", "consumer_secret": "YOUR_CLIENT_SECRET"}
```

Then link your account once (opens a browser, paste the code back):

```bash
venv/bin/python -m fbb.cli auth
```

**Hashtag Basketball.** Put your hashtag+ login in `.env` (`HASHTAG_EMAIL`, `HASHTAG_PASSWORD`). The tool logs in itself and keeps the session cookie in `hashtag_session.json`.

**Web login.** Set `WEB_AUTH_USER` and `WEB_AUTH_PASSWORD_HASH` in `.env` (see `docs/deploy.md` for generating the hash).

## Commands

| Command | What it does |
|---|---|
| `fbb whoami` | league, your team, current week and opponent |
| `fbb waiver [--drop NAME] [--punt TO] [--top 20] [--json]` | waiver recommendations |
| `fbb roster --drop "Sandro Mamukelashvili" [--top 8]` | should I drop this player, and for whom |
| `fbb roster matchup` / `fbb roster free-agents 30` | Hashtag planner summary / waiver wire |
| `fbb awards [--refresh] [--json]` | Putilla awards |
| `fbb transactions [--trends]` | most added / dropped |

Run them as `venv/bin/python -m fbb.cli ...`, or `venv/bin/pip install -e .` once to get a plain `fbb` command.

## Web site

```bash
scripts/start_webapp.sh      # http://127.0.0.1:5000, login from .env
```

`/` shows the awards, `/waiver` the optimizer. Deployment notes are in `docs/deploy.md`.

## Layout

```
fbb/            package: config, yahoo client, schedule, projection, matchup, optimizer, awards, hashtag, cli
web/            Flask app factory, routes, templates, static
tests/          pytest suite (fixtures under tests/fixtures)
docs/deploy.md  hosting notes
```

Model constants (blend weights, status multipliers, probability scales, drop-score weights) are all in `fbb/config.py` under `ModelParams`.

## Troubleshooting

- `Yahoo refused the request (403 ...)`: the app lost its Fantasy Sports permission or the consent expired. Check the app at developer.yahoo.com, then `fbb auth`.
- Token errors after a long gap: `fbb auth` again.
- ESPN schedule unreachable: the optimizer falls back to an average games-per-day estimate and says so (`games_source: fallback`).
