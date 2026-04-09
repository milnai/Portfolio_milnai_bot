# Market Bot — Quick Reference

## 🎯 Your Holdings (customize this first!)

Edit `market_bot.py` around line 30:

```python
HOLDINGS = {
    "RKLB": {"shares": 100, "avg_cost": 15.50, "conviction": "high"},
    "NVDA": {"shares": 50, "avg_cost": 128.00, "conviction": "high"},
    "MSFT": {"shares": 30, "avg_cost": 380.00, "conviction": "high"},
    "ALAB": {"shares": 75, "avg_cost": 35.00, "conviction": "medium"},
    "SCHD": {"shares": 200, "avg_cost": 55.00, "conviction": "low"},
    "INTC": {"shares": 40, "avg_cost": 28.00, "conviction": "medium"},
}
```

**Format:**
- `"shares"`: Number of shares you own
- `"avg_cost"`: Your average cost basis per share (for P&L calculation)
- `"conviction"`: Risk level (high/medium/low) — for future filtering

---

## 📊 What Each Report Includes

Every hour, you get:
1. **Market Metrics** — SPY, QQQ, DIA, VIX (price + % change)
2. **Portfolio P&L** — Each holding + total P&L
3. **Trading Signals** — 6-indicator scores (-6 to +6) for each stock
4. **Volume Spikes** — If any holding has >1.8x volume

---

## 🎨 Signal Scoring (-6 to +6)

```
🟢🟢 5-6    = STRONG BUY (all indicators bullish)
🟢   4      = BUY (most indicators bullish)
🟡   2-3    = WEAK (mixed signals)
⏸️   -1 to 1 = NO SIGNAL (wait)
🟠   -2 to -3 = WEAK SELL
🔴   -4     = SELL (most indicators bearish)
🔴🔴 -5 to -6 = STRONG SELL (all indicators bearish)
```

---

## ⚙️ Key Configuration Values

**In `market_bot.py`, line ~50:**

```python
# Signal Thresholds (when to alert)
SIGNAL_THRESHOLDS = {
    "strong_buy": 5,    # ← Change to 4 for more aggressive signals
    "buy": 4,
    "sell": -4,         # ← Change to -3 to catch early sells
    "strong_sell": -5,
}

# Volume spike threshold
VOLUME_SPIKE_RATIO = 1.8  # 1.8x = spike (change to 1.5 to be more sensitive)

# Volatility threshold
VOLATILITY_THRESHOLD = 0.03  # 3% move = alert (change to 0.02 for 2%)
```

---

## 🔔 When Reports Are Sent

Default schedule:
- **Hourly reports**: Every hour at `:00` (e.g., 1:00, 2:00, 3:00)
- **Signal alerts**: Every 30 minutes during US market hours (9:30 AM-4 PM ET)

To change, edit scheduler section (~line 430):

```python
# Report every 30 minutes instead:
CronTrigger(minute="*/30")

# Report only at 9 AM, 12 PM, 3 PM ET:
CronTrigger(minute=0, hour="14,17,20")

# Report only during market hours:
CronTrigger(minute=0, hour="14-20")  # 2-8 PM UTC = 9:30 AM-4 PM ET
```

---

## 📈 The 6 Indicators Explained

**EMA (Trend)**
- EMA 20 > EMA 50 = Uptrend = +1
- EMA 20 < EMA 50 = Downtrend = -1

**RSI (Momentum)**
- RSI < 30 = Oversold = +1
- RSI > 70 = Overbought = -1

**MACD (Momentum Building)**
- MACD > Signal = Bullish = +1
- MACD < Signal = Bearish = -1

**Bollinger Bands (Support/Resistance)**
- Price at lower band = Oversold = +1
- Price at upper band = Overbought = -1

**Volume (Conviction)**
- Volume 1.8x+ average = Confirm direction = +1 or -1
- Low volume = Weak signal = 0

**ADX (Trend Strength)**
- ADX > 25 = Strong trend = +1 (if EMA aligns)
- ADX < 20 = Choppy = 0 (ignore)

---

## 🚀 Deployment Checklist

- [ ] Run `python test_diagnostic.py` (should get Telegram test message)
- [ ] Edit `HOLDINGS` with your actual positions
- [ ] Run `python market_bot.py` (wait for first hourly report)
- [ ] Create `.env` file with bot token & chat ID
- [ ] Push to GitHub
- [ ] Create Railway account
- [ ] Deploy from GitHub
- [ ] Set Railway variables (token, chat ID)
- [ ] Wait for first hourly report ✅

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| No test message from diagnostic | Check bot token & chat ID (get with @BotFather) |
| "No messages" after 1 hour | Check Railway Variables (token, chat ID) |
| "Unauthorized" error | DM @BotFather, create new bot token |
| Slow reports | Normal (data fetch takes ~10-20 sec). Sent on schedule. |
| Portfolio shows $0 | Edit `HOLDINGS` with actual shares & cost basis |
| "Invalid token" error | Create new bot via @BotFather, update Railway |
| Lots of sell signals | Raise `SIGNAL_THRESHOLDS["sell"]` from -4 to -5 |
| Not enough buy signals | Lower `SIGNAL_THRESHOLDS["buy"]` from 4 to 3 |

---

## 📱 Telegram Formatting

- **🟢🟢** = Strong BUY (high confidence)
- **🟢** = BUY
- **🟡** = Weak (mixed signals)
- **🟠** = Weak SELL
- **🔴** = SELL
- **🔴🔴** = Strong SELL

- **📈** = Price up
- **📉** = Price down
- **⚡** = Volume spike
- **💼** = Portfolio update
- **🎯** = Signal score

---

## 📚 Further Reading

See `RAILWAY_SETUP.md` for:
- Complete deployment guide
- GitHub setup
- Railway troubleshooting
- Schedule customization
- Multiple chat IDs setup

---

## Quick Commands

**Test diagnostic:**
```bash
python test_diagnostic.py
```

**Run bot locally:**
```bash
python market_bot.py
```

**Test every minute (for debugging):**
Edit `market_bot.py` line 430, change:
```python
CronTrigger(minute=0)  # Every hour
```
To:
```python
CronTrigger(minute="*/1")  # Every minute
```
Then restart. Change back after testing.

**Create virtual environment:**
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Push to GitHub:**
```bash
git add .
git commit -m "Your message"
git push
```
