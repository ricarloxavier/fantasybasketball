# 🚀 Deploy to Railway.app (No GitHub Required!)

Railway lets you deploy directly from your computer without needing GitHub!

---

## Step 1: Install Railway CLI

```bash
brew install railway
```

Or if you don't have Homebrew:
```bash
npm install -g @railway/cli
```

---

## Step 2: Login to Railway

```bash
railway login
```

This will open your browser to authenticate.

---

## Step 3: Initialize Your Project

In your project folder:

```bash
cd /Users/ricarlo/gpt-y-fbb
railway init
```

Choose:
- **Project Name:** `putilla-awards` (or whatever you like)
- **Private:** Yes

---

## Step 4: Deploy!

```bash
railway up
```

That's it! Railway will:
1. ✅ Upload your code
2. ✅ Install dependencies from `requirements.txt`
3. ✅ Detect Flask automatically
4. ✅ Give you a live URL

---

## Step 5: Add OAuth Credentials

Since `oauth2.json` is in `.gitignore`, we need to add it as an environment variable:

```bash
# Copy your oauth2.json content
cat oauth2.json | pbcopy

# Then in Railway dashboard:
railway variables set YAHOO_OAUTH_JSON="paste-here"
```

Or do it in the Railway web dashboard:
1. Go to https://railway.app/dashboard
2. Click your project
3. Go to "Variables" tab
4. Add: `YAHOO_OAUTH_JSON` = (paste content of oauth2.json)

---

## Step 6: Get Your URL

```bash
railway domain
```

This will show your live URL! Share it with your leaguemates! 🎉

---

## 🔄 Updating Your Site

After making changes:

```bash
cd /Users/ricarlo/gpt-y-fbb
railway up
```

Done! 🚀

---

## 💰 Pricing

- **$5/month for hobby plan** (includes $5 credit)
- First month essentially free
- More than enough for a fantasy league site

---

## Alternative: Local Network Only (FREE)

If you don't want to pay anything, just share your local network URL:

1. Start server: `./start_webapp.sh`
2. Find your IP: `ipconfig getifaddr en0`
3. Share: `http://YOUR_IP:5000`

**Pros:** 
- ✅ Completely free
- ✅ Your data stays on your computer

**Cons:**
- ❌ Only works when your computer is on
- ❌ Only works on same WiFi network

---

Made with 🖕 for Chantasy League
