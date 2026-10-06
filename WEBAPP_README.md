# 🏀 Fantasy Basketball Web App

A local web app with:
- Putilla Awards dashboard
- Waiver Win-Probability Optimizer for current matchup

## 📁 Files

- **`putilla_awards_v2.py`** - Original awards calculator (unchanged, use for local running)
- **`awards_api.py`** - Wrapper to make awards data accessible to web app
- **`waiver_optimizer_api.py`** - Waiver add/drop optimizer API logic
- **`web_app.py`** - Flask web server
- **`templates/awards.html`** - Beautiful frontend interface
- **`templates/waiver.html`** - Waiver optimizer frontend

## 🚀 Quick Start

### 1. Run Locally

```bash
cd /Users/ricarlo/gpt-y-fbb
source .venv/bin/activate
python web_app.py
```

Then open:
- **http://localhost:5000/** (Awards)
- **http://localhost:5000/waiver** (Waiver Optimizer)

### 2. Share with Leaguemates (Local Network)

Your leaguemates can access it if they're on the same WiFi:

1. Start the server (see above)
2. Find your local IP address:
   ```bash
   # On Mac/Linux:
   ifconfig | grep "inet " | grep -v 127.0.0.1
   
   # Or just:
   ipconfig getifaddr en0
   ```
3. Share the URL: `http://YOUR_IP:5000` (e.g., `http://192.168.1.100:5000`)

⚠️ **Note:** This only works while your computer is on and on the same network!

## 🌐 Deploy to the Internet (Free)

To make it accessible anywhere, deploy to a free hosting service:

### Option A: Render.com (Recommended)

1. Push your code to GitHub
2. Go to [render.com](https://render.com) and sign in
3. Create new "Web Service"
4. Connect your GitHub repo
5. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python web_app.py`
6. Deploy!

### Option B: Railway.app

1. Go to [railway.app](https://railway.app)
2. "New Project" → Deploy from GitHub
3. It auto-detects Flask apps
4. Done!

### Option C: PythonAnywhere

1. Go to [pythonanywhere.com](https://www.pythonanywhere.com)
2. Free tier: Upload files
3. Configure WSGI file
4. Get URL like: `yourusername.pythonanywhere.com`

## 📦 Requirements

Create `requirements.txt` for deployment:

```bash
cd /Users/ricarlo/gpt-y-fbb
pip freeze > requirements.txt
```

## 🔄 Update Awards

The website has a "Refresh Awards" button that recalculates on demand.

You can also manually refresh by visiting: **http://localhost:5000/api/awards/refresh**

## 🎯 Waiver Optimizer

Visit **http://localhost:5000/waiver** to run matchup waiver analysis.

What it does:
- Compares your projected remaining-week totals vs your current opponent
- Estimates category win odds + total matchup win probability
- Uses current score + remaining games (not just rest-of-week projection)
- Evaluates add/drop combinations from free agents and ranks by win-probability gain
- Highlights category flips and the most likely drop candidates
- Supports manual drop mode (evaluate only specific player(s) you choose)
- Shows roster percent-owned and Yahoo status/news signals

API endpoint:
- **http://localhost:5000/api/waiver/analyze**
- Query params: `free_agent_pool`, `drop_candidates`, `max_results`, `punt`, `drop_player`

## 🎨 Customization

Edit `templates/awards.html` to customize:
- Colors (change the gradient in the `<style>` section)
- Award emojis (in the `awardInfo` object in `<script>`)
- League name (in the header)

## 🔒 Security Note

The current version requires HTTP Basic Auth. Set `APP_USERNAME` and
`APP_PASSWORD` before starting it. For Render, follow
[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) and provide Yahoo OAuth credentials
as a secret environment variable, never in GitHub.

## 💡 Tips

- Run `putilla_awards_v2.py` directly for detailed console output
- Use the web app for sharing with leaguemates
- Awards update automatically when you refresh the page
- Mobile-friendly responsive design

## 🆘 Troubleshooting

**Error: "OAuth token expired"**
- Run `putilla_awards_v2.py` first to refresh tokens

**Error: "Module not found"**
- Make sure you're in the virtual environment: `source venv/bin/activate`

**Awards not loading**
- Check terminal for error messages
- Ensure `oauth2.json` is in the same directory

**Can't connect from other devices**
- Check firewall settings
- Make sure you're using your actual IP (not 127.0.0.1)

---

Made with ❤️ for Chantasy League
