#!/usr/bin/env python3
"""
Market Bot Diagnostic — Test Telegram connectivity and API access
Run this to identify what's broken before deploying to Railway
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# Check Python version
print(f"🐍 Python: {sys.version}")
print()

# ============================================================================
# 1. CHECK ENVIRONMENT VARIABLES
# ============================================================================
print("="*60)
print("1️⃣  CHECKING ENVIRONMENT VARIABLES")
print("="*60)

from dotenv import load_dotenv

# Load .env from current directory
env_path = Path(".env")
if env_path.exists():
    load_dotenv(".env")
    print(f"✅ Found .env file")
else:
    print(f"❌ No .env file found in {Path.cwd()}")
    print(f"   Create one by copying .env.example")
    sys.exit(1)

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if not bot_token:
    print("❌ TELEGRAM_BOT_TOKEN not set in .env")
    sys.exit(1)
else:
    print(f"✅ Bot token found (length: {len(bot_token)})")
    print(f"   Format check: {bot_token[:10]}...{bot_token[-5:]}")

if not chat_id:
    print("❌ TELEGRAM_CHAT_ID not set in .env")
    sys.exit(1)
else:
    print(f"✅ Chat ID found: {chat_id}")

print()

# ============================================================================
# 2. CHECK DEPENDENCIES
# ============================================================================
print("="*60)
print("2️⃣  CHECKING DEPENDENCIES")
print("="*60)

packages = {
    "telegram": "python-telegram-bot",
    "yfinance": "yfinance",
    "pandas": "pandas",
    "numpy": "numpy",
    "apscheduler": "apscheduler",
}

missing = []
for module, pip_name in packages.items():
    try:
        __import__(module)
        print(f"✅ {pip_name}")
    except ImportError:
        print(f"❌ {pip_name} NOT INSTALLED")
        missing.append(pip_name)

if missing:
    print()
    print("Install missing packages:")
    print(f"pip install {' '.join(missing)}")
    sys.exit(1)

print()

# ============================================================================
# 3. TEST TELEGRAM CONNECTION
# ============================================================================
print("="*60)
print("3️⃣  TESTING TELEGRAM CONNECTION")
print("="*60)

import asyncio
from telegram import Bot

async def test_telegram():
    try:
        bot = Bot(token=bot_token)
        print(f"⏳ Connecting to Telegram API...")
        
        # Test getMe (simplest API call)
        me = await bot.get_me()
        print(f"✅ Connected to Telegram!")
        print(f"   Bot name: @{me.username}")
        print(f"   Bot ID: {me.id}")
        print()
        
        # Test sending message
        print(f"⏳ Sending test message to chat {chat_id}...")
        try:
            message = await bot.send_message(
                chat_id=chat_id,
                text="🤖 Market Bot Diagnostic Test\n\n"
                     "✅ Telegram connection working!\n"
                     f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            print(f"✅ Message sent successfully!")
            print(f"   Message ID: {message.message_id}")
            print(f"   Chat ID: {message.chat_id}")
        except Exception as e:
            print(f"❌ Failed to send message: {e}")
            print()
            print("Common fixes:")
            print("  1. Chat ID is wrong (check with @BotFather /getUpdates)")
            print("  2. Bot hasn't been added to the chat")
            print("  3. Bot token is invalid")
            return False
        
        return True
    
    except Exception as e:
        print(f"❌ Telegram connection failed: {e}")
        print()
        print("Common fixes:")
        print("  1. Check bot token in .env")
        print("  2. Token might be expired (create new with @BotFather)")
        print("  3. Internet connection issue")
        return False

success = asyncio.run(test_telegram())
print()

# ============================================================================
# 4. TEST DATA FETCHING
# ============================================================================
print("="*60)
print("4️⃣  TESTING DATA FETCHING")
print("="*60)

import yfinance as yf

test_tickers = ["NVDA", "SPY"]
for ticker in test_tickers:
    try:
        print(f"⏳ Fetching {ticker}...")
        data = yf.download(ticker, period="5d", progress=False)
        if not data.empty:
            price = float(data['Close'].iloc[-1])
            print(f"✅ {ticker}: ${price:.2f}")
        else:
            print(f"❌ {ticker}: No data returned")
    except Exception as e:
        print(f"❌ {ticker}: {e}")

print()

# ============================================================================
# 5. TEST INDICATORS
# ============================================================================
print("="*60)
print("5️⃣  TESTING INDICATOR CALCULATION")
print("="*60)

try:
    print(f"⏳ Fetching 3-month data for NVDA...")
    data = yf.download("NVDA", period="3mo", progress=False)
    
    if not data.empty:
        print(f"✅ Got {len(data)} days of data")
        
        # Quick EMA calculation
        import pandas as pd
        close = data['Close']
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        
        ema20_val = float(ema20.iloc[-1])
        ema50_val = float(ema50.iloc[-1])
        
        print(f"   EMA20: ${ema20_val:.2f}")
        print(f"   EMA50: ${ema50_val:.2f}")
        print(f"   Signal: {'Uptrend ⬆️' if ema20_val > ema50_val else 'Downtrend ⬇️'}")
    else:
        print("❌ No data fetched")

except Exception as e:
    print(f"❌ Indicator test failed: {e}")

print()

# ============================================================================
# 6. SUMMARY
# ============================================================================
print("="*60)
print("6️⃣  SUMMARY")
print("="*60)

if success:
    print("✅ All systems operational!")
    print()
    print("Next steps:")
    print("  1. Check your Telegram chat for the test message above ☝️")
    print("  2. Run: python market_bot.py")
    print("  3. Bot will send first report at top of next hour")
    print()
    print("To debug timing:")
    print("  - Edit market_bot.py, change CronTrigger(minute=0) to CronTrigger(minute='*/1')")
    print("  - This will send reports EVERY MINUTE for testing")
    print("  - After testing, change back to minute=0 for hourly reports")
else:
    print("❌ Some issues found. Check above for fixes.")
    print()
    print("Most common issue: Wrong TELEGRAM_CHAT_ID")
    print("  Get it here: https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates")
    print("  (replace <YOUR_TOKEN> with actual token)")

print()
