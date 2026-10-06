# Yahoo Fantasy Basketball Adds/Drops

Python helper to list the most added and dropped players in a Yahoo Fantasy Basketball league.

## Web App (Awards + Waiver Optimizer)

Run:

```bash
source .venv/bin/activate
python web_app.py
```

Then open:
- `http://localhost:5000/` for Putilla Awards
- `http://localhost:5000/waiver` for matchup waiver recommendations ranked by estimated win-probability gain

For a hosted deployment, see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).

## Setup
1) Create a Yahoo Fantasy Sports app  
   - Go to https://developer.yahoo.com/apps and create an app (any name).  
   - Set Application Type to `Web`. Redirect URI can be `oob` (out-of-band).  
   - Copy the `Client ID` and `Client Secret`.
   - Apply for [Yahoo Fantasy Sports API access](https://sports.yahoo.com/developer/access/)
     using that Client ID. Yahoo reviews applications; OAuth login alone does not
     grant Fantasy Sports API access. The awards and waiver pages require read access.

2) Create `oauth2.json` in this folder:
```json
{
  "consumer_key": "YOUR_CLIENT_ID",
  "consumer_secret": "YOUR_CLIENT_SECRET"
}
```
The first run will store access/refresh tokens in this file automatically.

3) Install dependencies (Python 3.10+; deployment uses 3.11):
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4) Authenticate once to link your Yahoo account:
```bash
python yahoo_add_drop.py --auth
```
This will open a browser for you to sign in and consent. Tokens are saved back to `oauth2.json`.

## Usage
```bash
python yahoo_add_drop.py --league-id 10145 --top 20
```
- `--league-id`: numeric league id from your Yahoo URL (no game key prefix). Default is `10145`.
- `--top`: how many players to list for adds/drops.
- `--oauth-file`: path to `oauth2.json` if you keep it elsewhere.

The script will discover the correct full league key for your account, page through all add/drop transactions for the season, and print the most added and dropped players.

## Notes
- Your Yahoo account must have access to the league id you specify.
- If you see auth errors later, re-run with `--auth` to refresh tokens. Tokens live in `oauth2.json`.

## Login configuration for this packaged copy

Set `APP_USERNAME` and `APP_PASSWORD` in your environment before starting the web app. Choose your own values; the original embedded login has been removed. Recreate `oauth2.json` locally using the setup instructions above.

```bash
export APP_USERNAME="your-username"
export APP_PASSWORD="your-chosen-password"
python web_app.py
```
