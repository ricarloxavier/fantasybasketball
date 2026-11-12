# 🎉 Putilla Awards Website - Ready to Share!

## ✅ What I Created

### **Files Created:**
1. **`web_app.py`** - Flask web server
2. **`awards_api.py`** - Wrapper that connects your existing awards script to the web
3. **`templates/awards.html`** - Beautiful frontend with animations
4. **`start_webapp.sh`** - Easy startup script
5. **`WEBAPP_README.md`** - Full documentation

### **Files Untouched:**
- **`putilla_awards_v2.py`** - Your original script still works exactly the same!

---

## 🚀 How to Start the Website

### Quick Start (Local):
```bash
cd /Users/ricarlo/gpt-y-fbb
./start_webapp.sh
```

Then visit: **http://localhost:5000**

---

## 📱 Share with Leaguemates

### Option 1: Local Network (Same WiFi)

1. Start the server: `./start_webapp.sh`
2. Find your IP:
   ```bash
   ipconfig getifaddr en0
   ```
3. Share URL: `http://YOUR_IP:5000`

⚠️ **Only works while your computer is on!**

### Option 2: Deploy to Internet (Recommended)

**Best: Render.com (Free)**
1. Create account at [render.com](https://render.com)
2. Connect your GitHub repo (push this folder)
3. Create "Web Service"
4. It auto-deploys!
5. Get permanent URL like: `putilla-awards.onrender.com`

**Alternative: Railway.app**
- Even easier auto-deploy
- Free tier available

---

## 🎨 What It Looks Like

The website features:
- 🏆 **Animated award cards** with emojis
- 🥇 **Gold/Silver/Bronze** borders for top awards
- 📱 **Mobile-responsive** design
- 🔄 **Refresh button** to update awards
- 🎨 **Purple gradient** theme (easily customizable)

Awards displayed:
1. 👑 **Putilla Suprema** - Most games started
2. 🥈 **Putilla de Plata** - 2nd place
3. 🥉 **Putilla de Bronce** - 3rd place  
4. ⚡ **Putilla Más Activa** - Most roster churn
5. 💎 **Putilla Fiel** - Team loyalty award
6. 💰 **La Putilla Escondida** - Best value per game
7. 🌟 **Putilla MVP** - Highest total value
8. 🗑️ **Putilla Basura** - Worst pickup

---

## 🔧 Customize It

### Change Colors:
Edit `templates/awards.html`, line ~20:
```css
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
```

### Change League Name:
Edit `templates/awards.html`, line ~200:
```html
<h1>🏆 LOS PUTILLA AWARDS 🏆</h1>
```

### Change Award Emojis:
Edit `templates/awards.html`, line ~290 (the `awardInfo` object)

---

## 🎯 Next Steps

### For Local Use:
✅ Just run `./start_webapp.sh` whenever you want to view

### For Sharing:
1. **Push to GitHub** (if you haven't)
   ```bash
   git add .
   git commit -m "Add Putilla Awards website"
   git push
   ```

2. **Deploy to Render.com**
   - Sign up at render.com
   - "New Web Service"
   - Connect GitHub
   - Select this repo
   - Deploy!

3. **Share the URL** with your league

---

## 💡 Pro Tips

- Run `putilla_awards_v2.py` locally for detailed analysis
- Use the website for sharing with league
- Awards auto-calculate on page load
- Refresh button recalculates with latest data
- Works great on mobile!

---

## 🆘 Help

**Website won't start?**
```bash
cd /Users/ricarlo/gpt-y-fbb
source venv/bin/activate
pip install flask
python web_app.py
```

**Awards won't load?**
- Make sure `oauth2.json` is present
- Run `putilla_awards_v2.py` first to refresh OAuth

**Want to test locally first?**
```bash
python awards_api.py
```

---

## 🎊 You're Done!

Your Putilla Awards are now ready to share with the world!

**Local:** `http://localhost:5000`  
**Network:** `http://YOUR_IP:5000`  
**Internet:** Deploy to Render.com for permanent URL

Enjoy showing off those waiver wire wins! 🏆🔥
