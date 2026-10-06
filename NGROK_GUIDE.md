# 🌍 Share Putilla Awards Worldwide with ngrok (FREE!)

ngrok creates a secure public tunnel to your local server. Perfect for sharing with leaguemates around the world!

---

## ✅ Quick Start (3 Steps)

### Step 1: Sign up for ngrok (Free)
```bash
# Go to: https://dashboard.ngrok.com/signup
# Sign up (it's free!)
# Copy your auth token
```

### Step 2: Connect your account
```bash
ngrok config add-authtoken YOUR_TOKEN_HERE
```

### Step 3: Start everything!

**Terminal 1 - Start Flask:**
```bash
cd /Users/ricarlo/gpt-y-fbb
./start_webapp.sh
```

**Terminal 2 - Start ngrok:**
```bash
ngrok http 5000
```

That's it! ngrok will show you a public URL like:
```
https://abc123-def456.ngrok-free.app
```

**Share this URL with your leaguemates!** 🎉

---

## 🎯 What You'll See

When you run `ngrok http 5000`, you'll see:

```
Session Status                online
Account                       Your Name (Plan: Free)
Version                       3.x.x
Region                        United States (us)
Latency                       -
Web Interface                 http://127.0.0.1:4040
Forwarding                    https://abc-123.ngrok-free.app -> http://localhost:5000
```

The **Forwarding** line shows your public URL! 🚀

---

## 💡 Important Notes

### FREE Plan Limits:
- ✅ Unlimited tunnels
- ✅ HTTPS included
- ✅ 40 connections/minute (plenty for a league!)
- ⚠️ URL changes each time you restart ngrok
- ⚠️ Has ngrok branding page on first visit

### Keeping Same URL (Optional $8/month):
- Upgrade to paid plan for permanent URL
- Worth it if you use this weekly/monthly

### While It's Running:
- ✅ Keep both terminals open
- ✅ Works globally - China, Europe, anywhere!
- ✅ HTTPS secure automatically
- ✅ View real-time traffic at http://localhost:4040

---

## 🔄 Workflow

**When you want to share awards:**

1. Open 2 terminals
2. Terminal 1: `./start_webapp.sh`
3. Terminal 2: `ngrok http 5000`
4. Copy the ngrok URL (https://...)
5. Send to league group chat
6. Done! Leave running while they view

**To stop:**
- Press Ctrl+C in both terminals

---

## 🛡️ Security

- ✅ ngrok URLs are hard to guess (random string)
- ✅ HTTPS encryption included
- ✅ Only people with the URL can access
- ✅ You can see all visitors in ngrok dashboard

**Want more security?**
Add basic password protection to your Flask app (I can help with this).

---

## 🎁 Pro Tips

### 1. View Traffic
While ngrok is running, visit:
```
http://localhost:4040
```
See who's visiting in real-time!

### 2. Share Instructions
Send this to your league:
```
🏆 PUTILLA AWARDS ARE LIVE! 🏆

Check out the awards at:
https://your-ngrok-url.ngrok-free.app

(Click through the ngrok banner)

Updates weekly! 🔥
```

### 3. Keep URL Private
Don't share the URL publicly - only in your league chat.

### 4. Schedule Viewing Times
Since you need to run it manually:
- "Awards live tonight 8-10pm"
- "Check it out this weekend"
- Or just leave it running 24/7 on your Mac

---

## 🆘 Troubleshooting

**"ngrok: command not found"**
```bash
brew install ngrok
```

**"Tunnel not found"**
- Make sure Flask is running first
- Check Flask is on port 5000

**"Authentication failed"**
```bash
ngrok config add-authtoken YOUR_TOKEN
```

**Leaguemates see ngrok branding page**
- Normal for free plan
- They just click "Visit Site" button once
- Won't see it again that session

---

## 💰 Cost Comparison

| Option | Cost | Pros | Cons |
|--------|------|------|------|
| **ngrok Free** | $0 | Instant, no setup, global | URL changes, branding page |
| **ngrok Pro** | $8/mo | Permanent URL, no branding | Costs money |
| **Railway** | $5/mo | Always online | Need to upload code |
| **Local Network** | $0 | Free, private | Only works on same WiFi |

**Recommendation:** Start with ngrok free, upgrade later if needed!

---

Made with 🖕 for Chantasy League
