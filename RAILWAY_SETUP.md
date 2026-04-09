# Telegram Market Bot — Railway Deployment Guide

## What This Bot Does

- **Hourly Market Reports** (every hour, 24/7)
  - Portfolio P&L for all holdings
  - Market metrics (SPY, QQQ, DIA, VIX)
  - Trading signals based on 6-indicator system
  - Volume spikes and volatility alerts

- **Real-Time Data** (fresh API calls each hour)
  - Fetches latest prices from Yahoo Finance
  - Calculates technical indicators (EMA, RSI, MACD, Bollinger Bands, ADX)
  - Generates signal scores (-6 to +6)
  - Confidence levels for each signal

- **Smart Scheduling**
  - Full reports every hour on the hour (`:00`)
  - Alert signals every 30 minutes during market hours
  - Lightweight reporting after hours

---

## Prerequisites

1. **Telegram Bot Token**
   - DM @BotFather on Telegram
   - Send `/newbot`
   - Choose a name (e.g., "My Market Bot")
   - Save the token (looks like: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

2. **Telegram Chat ID**
   - Send a message to your bot
   - Go to `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - Copy the `chat.id` (usually a negative number like `-1234567890` for groups)

3. **GitHub Account** (to host code)
4. **Railway Account** (free tier works)

---

## Step 1: Prepare Files Locally

1. Create folder: `C:\Portfolio_milnai_bot\`

2. Copy all these files into the folder:
   ```
   market_bot.py          (the main bot)
   requirements.txt       (dependencies)
   .env.example          (copy as .env and fill in)
   test_diagnostic.py    (testing script)
   Procfile              (Railway config)
   .gitignore            (git config)
   runtime.txt           (Python version)
   ```

3. **Create `.env` file** (copy from `.env.example`):
   ```
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   LOG_LEVEL=INFO
   ```
   ⚠️ Keep `.env` **private** — never commit to GitHub!

4. **Customize `market_bot.py`** (optional):
   - Edit the `HOLDINGS` dictionary at the top with your stocks:
     ```python
     HOLDINGS = {
         "RKLB": {"shares": 100, "avg_cost": 15.50, "conviction": "high"},
         "NVDA": {"shares": 50, "avg_cost": 128.00, "conviction": "high"},
         # ... add/remove stocks as needed
     }
     ```

---

## Step 2: Test Locally (Windows)

1. **Create Python virtual environment:**
   ```bash
   cd C:\Portfolio_milnai_bot
   python -m venv venv
   venv\Scripts\activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run diagnostic test:**
   ```bash
   python test_diagnostic.py
   ```
   
   ✅ This will:
   - Check your `.env` file
   - Verify all dependencies are installed
   - **Send a test message to your Telegram** ← most important!
   - Test data fetching from Yahoo Finance
   - Test indicator calculations

   **If you receive a test message in Telegram, everything is working!** ✅

4. **Run the actual bot:**
   ```bash
   python market_bot.py
   ```
   
   ✅ You should see:
   ```
   INFO - Scheduler started. Sending reports hourly + alerts every 30min
   ```

5. **Wait for top of next hour** (it will send first report at `:00`)
   - Example: If it's 2:47 PM, you'll get your first report at 3:00 PM
   - Check your Telegram chat for incoming messages

6. **Stop the bot:** Press `Ctrl+C`

---

## Step 3: Push to GitHub

1. **Create a new GitHub repo:**
   - Go to github.com → New Repository
   - Name: `Portfolio_milnai_bot`
   - **Do NOT add README/LICENSE** (create empty repo)
   - Copy the commands it shows you

2. **Push your code:**
   ```bash
   cd C:\Portfolio_milnai_bot
   git init
   git add .
   git commit -m "Initial market bot setup"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/Portfolio_milnai_bot.git
   git push -u origin main
   ```

3. **Verify on GitHub:**
   - Open your repo, should see files listed
   - ⚠️ Make sure `.env` is **NOT** uploaded (check .gitignore)

---

## Step 4: Deploy to Railway

1. **Go to railway.app** → Sign up/Login

2. **Create a new project:**
   - Click "New Project"
   - Select "Deploy from GitHub"
   - Authorize Railway to access GitHub
   - Select your `Portfolio_milnai_bot` repo

3. **Add Environment Variables:**
   - In the Railway dashboard, go to "Variables"
   - Add these 2 variables:
     ```
     TELEGRAM_BOT_TOKEN = (paste your bot token)
     TELEGRAM_CHAT_ID = (paste your chat ID)
     ```

4. **Add Python version** (optional but recommended):
   - In "Config" or "Environments", set Python to `3.11`

5. **Deploy:**
   - Click "Deploy"
   - Watch the build log — should see `✅ Deployment successful`

6. **Verify it's running:**
   - Check "Logs" in Railway dashboard
   - Should see: `INFO - Scheduler started...`
   - Wait for the next hour mark — you should get a Telegram message ✅

---

## Step 5: Monitor & Update

### Check the bot is working:
- **Railway Dashboard** → Click your project → "Logs" tab
- Should see messages like:
  ```
  ✅ Sent hourly report to 123456
  ✅ Sent alert for 2 strong signals
  ```

### Update the bot:
1. Make changes locally (edit `market_bot.py`)
2. Test locally: `python market_bot.py`
3. Commit & push:
   ```bash
   git add .
   git commit -m "Updated signal thresholds"
   git push
   ```
4. Railway auto-redeploys on push ✅

### Troubleshooting:

| Issue | Solution |
|-------|----------|
| Bot not sending messages | Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in Railway Variables |
| Error in logs: "No module yfinance" | Push `requirements.txt` and redeploy |
| Slow data fetching | Normal (Yahoo Finance can be slow). Reports sent on-schedule. |
| Telegram token invalid | DM @BotFather again, create new bot, update Railway variables |
| Portfolio P&L shows $0 | Edit `HOLDINGS` dict with your actual shares & cost basis |

---

## Customization Guide

### Change Reporting Schedule

In `market_bot.py`, find the scheduler section (~line 430):

**More frequent reports (every 30 min):**
```python
scheduler.add_job(
    send_hourly_report,
    CronTrigger(minute="*/30"),  # Changed from minute=0
    ...
)
```

**Only market hours (9:30 AM - 4 PM ET):**
```python
scheduler.add_job(
    send_hourly_report,
    CronTrigger(minute=0, hour="14-20"),  # Only 2-8 PM UTC (9:30 AM-4 PM ET)
    ...
)
```

### Adjust Signal Thresholds

Find `SIGNAL_THRESHOLDS` (~line 50):
```python
SIGNAL_THRESHOLDS = {
    "strong_buy": 5,      # Change to 4 for more buy signals
    "buy": 4,
    "sell": -4,
    "strong_sell": -5,
}
```

### Change Holdings

Edit the `HOLDINGS` dictionary (~line 30):
```python
HOLDINGS = {
    "RKLB": {"shares": 100, "avg_cost": 15.50, "conviction": "high"},
    "NVDA": {"shares": 50, "avg_cost": 128.00, "conviction": "high"},
    # Add more or remove as needed
}
```

---

## Cost & Limits

- **Railway**: Free tier includes 5GB bandwidth/month (plenty for hourly reports)
- **Telegram Bot**: Free (no limits)
- **Yahoo Finance**: Free (no API key needed)
- **Total Cost**: $0/month ✅

---

## FAQ

**Q: Can I track more stocks?**
A: Yes! Add them to `HOLDINGS`. Each stock = 1 API call/hour, so 10 stocks = 10 calls/hour (still well within free limits).

**Q: Can I change when reports are sent?**
A: Yes! Edit the `CronTrigger()` in the scheduler section. See examples in Customization Guide.

**Q: What if Yahoo Finance is down?**
A: Bot will log an error and skip that report. No crashes. It'll try again next hour.

**Q: Can I send alerts to multiple Telegram chats?**
A: Yes! Ask for help with this in next session.

**Q: How do I stop the bot?**
A: Go to Railway → Click project → "Settings" → "Pause" (or delete the service)

---

## Next Steps

1. ✅ Run `python test_diagnostic.py` locally
2. ✅ Get test message in Telegram
3. ✅ Run `python market_bot.py` and wait for hourly report
4. ✅ Push to GitHub
5. ✅ Deploy to Railway
6. ✅ Monitor logs for 24 hours to confirm stability

---

## Support

If you hit issues:
1. Check **Railway Logs** (project → Logs tab)
2. Run **test_diagnostic.py** to identify the problem
3. Check **Telegram token/chat ID** are correct in Variables
4. Run `python market_bot.py` locally to test

Good luck! 🚀
