# Deploy on Render

This folder includes a Render Blueprint in `render.yaml`. It runs the Flask app
with Gunicorn, checks `/health`, and uses Python 3.11.

## Before you deploy

1. Create `oauth2.json` locally and complete Yahoo authorization as described in
   `README.md`. Keep this file private. It is ignored by Git.
2. The source is published at
   [ricarloxavier/fantasybasketball](https://github.com/ricarloxavier/fantasybasketball).
   This repository is public. Do not add `oauth2.json` or any password to it.
3. In Render, create a new Blueprint from that repository. Render reads
   `render.yaml` and prompts for these secret environment variables:
   - `APP_USERNAME`: username for the app's HTTP Basic Auth login
   - `APP_PASSWORD`: a strong, unique password for that login
   - `YAHOO_OAUTH_JSON`: the **entire contents** of your authorized `oauth2.json`
4. Deploy the Blueprint. Open the service's Render URL and sign in. `/health`
   should return `{"status":"ok"}` without login; `/` and `/waiver` require login.

The start script copies `YAHOO_OAUTH_JSON` to a private writable file so the
Yahoo library can refresh its access token. Render's free instance filesystem is
ephemeral, so the copy and server-side waiver preferences can disappear on a
restart. Keep the source JSON safe and update the Render secret if Yahoo rotates
its refresh token. Browser-local waiver preferences remain available in that
browser.

If the service starts but Yahoo data fails to load, check that the JSON contains
working authorization tokens, the Yahoo app has access to the configured league,
and the Render logs show no token refresh error. Never paste tokens into logs or
issues.

Render references: [Flask deployment](https://render.com/docs/deploy-flask),
[Blueprints](https://render.com/docs/infrastructure-as-code), and
[environment secrets](https://render.com/docs/configure-environment-variables).
