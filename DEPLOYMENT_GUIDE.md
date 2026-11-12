# 🚀 Deployment Guide for Render.com

## 📋 Prerequisites

Before deploying, you need:
1. ✅ `requirements.txt` (created)
2. ✅ `.gitignore` (created)
3. ✅ Updated `web_app.py` for production (done)
4. 📁 GitHub account
5. 🌐 Render.com account (free)

---

## Step 1: Push to GitHub

### Initialize Git (if not already done):
```bash
cd /Users/ricarlo/gpt-y-fbb
git init
git add .
git commit -m "Initial commit: Putilla Awards web app"
```

### Create GitHub repo:
1. Go to https://github.com/new
2. Name it: `putilla-awards` (or whatever you like)
3. Make it **PRIVATE** (your league, your data!)
4. Don't initialize with README

### Push to GitHub:
```bash
git remote add origin https://github.com/YOUR_USERNAME/putilla-awards.git
git branch -M main
git push -u origin main
```

---

## Step 2: Deploy on Render.com

### A. Create Render Account
1. Go to https://render.com
2. Sign up with GitHub (easiest)

### B. Create Web Service
1. Click "New" → "Web Service"
2. Connect your GitHub repo `putilla-awards`
3. Configure:
   - **Name:** `putilla-awards` (or your choice)
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python web_app.py`
   - **Plan:** `Free`

### C. Add Environment Variable
Since `oauth2.json` is NOT in GitHub (sensitive!), we need to add it as an environment variable:

1. In Render dashboard, go to your service
2. Click "Environment" tab
3. Add variable:
   - **Key:** `YAHOO_OAUTH_JSON`
   - **Value:** Copy-paste the contents of your `oauth2.json` file

---

## Step 3: Update Code to Use Environment Variable

We need to modify `awards_api.py` to read OAuth from environment variable in production.

Add this at the top of `awards_api.py`:

```python
import os
import json

def ensure_oauth(oauth_file='oauth2.json'):
    """Get OAuth, from file locally or env var in production."""
    # Try environment variable first (for Render deployment)
    if 'YAHOO_OAUTH_JSON' in os.environ:
        oauth_json = json.loads(os.environ['YAHOO_OAUTH_JSON'])
        # Create temporary file
        with open('oauth2.json', 'w') as f:
            json.dump(oauth_json, f)
    
    return OAuth2(None, None, from_file=oauth_file)
```

---

## Step 4: Deploy!

1. Click "Create Web Service" in Render
2. Wait 3-5 minutes for build
3. Your site will be live at: `https://putilla-awards.onrender.com`

---

## 🎉 You're Live!

Share the URL with your leaguemates:
```
https://your-app-name.onrender.com
```

---

## ⚠️ Important Notes

### Free Tier Limitations:
- **Sleeps after 15 min of inactivity**
  - First load may take 30-60 seconds to "wake up"
  - Subsequent loads are fast
  
- **750 hours/month free**
  - More than enough for a fantasy league

### Keeping It Awake (Optional):
Use a free service like UptimeRobot to ping your site every 10 minutes:
1. Go to https://uptimerobot.com
2. Add your Render URL
3. Ping interval: 10 minutes

---

## 🔄 Updating Your Site

After making changes locally:

```bash
cd /Users/ricarlo/gpt-y-fbb
git add .
git commit -m "Updated awards"
git push
```

Render auto-deploys when you push to GitHub! 🎉

---

## 🆘 Troubleshooting

**"Module not found" error:**
- Check that `requirements.txt` includes all packages
- Rebuild in Render dashboard

**OAuth errors:**
- Make sure `YAHOO_OAUTH_JSON` environment variable is set
- Copy the ENTIRE contents of `oauth2.json`

**Site won't load:**
- Check Render logs in dashboard
- First load after sleep takes 30-60 seconds

---

## 💡 Pro Tips

1. **Custom Domain:** Render allows custom domains on free tier
2. **HTTPS:** Render provides free SSL automatically
3. **Logs:** View real-time logs in Render dashboard
4. **Rollback:** Render keeps deployment history

---

Made with 🖕 for Chantasy League
