#!/usr/bin/env python3
"""
Test script to verify Finnhub API and Telegram connection
"""

import requests
import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot

# Load environment variables
load_dotenv()

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

print("="*50)
print("🧪 TESTING FINNHUB API & TELEGRAM")
print("="*50)

# ============================================================================
# TEST 1: Check environment variables
# ============================================================================

print("\n✅ TEST 1: Environment Variables")
print("-" * 50)

telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
telegram_chat = os.getenv("TELEGRAM_CHAT_ID")
finnhub_key = os.getenv("FINNHUB_API_KEY")

print(f"TELEGRAM_BOT_TOKEN: {telegram_token[:20] if telegram_token else '❌ NOT SET'}...")
print(f"TELEGRAM_CHAT_ID: {telegram_chat if telegram_chat else '❌ NOT SET'}")
print(f"FINNHUB_API_KEY: {finnhub_key[:20] if finnhub_key else '❌ NOT SET'}...")

if not all([telegram_token, telegram_chat, finnhub_key]):
    print("\n❌ ERROR: Missing environment variables!")
    print("Make sure .env file has all 3 variables set")
    exit(1)

print("\n✅ All variables loaded successfully!")

# ============================================================================
# TEST 2: Test Finnhub API - Single Stock
# ============================================================================

print("\n✅ TEST 2: Finnhub API - Stock Quote")
print("-" * 50)

try:
    url = f"{FINNHUB_BASE_URL}/quote"
    params = {"symbol": "RKLB", "token": FINNHUB_API_KEY}
    
    print(f"Fetching RKLB from Finnhub...")
    response = requests.get(url, params=params, timeout=10)
    
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Response: {data}")
        
        if "c" in data and data["c"]:
            price = data["c"]
            print(f"\n✅ RKLB Price: ${price:.2f}")
            print("✅ Finnhub API is working!")
        else:
            print(f"\n❌ No price data in response!")
    else:
        print(f"❌ HTTP Error {response.status_code}")
        print(f"Response: {response.text}")

except Exception as e:
    print(f"❌ Error fetching from Finnhub: {e}")

# ============================================================================
# TEST 3: Test all holdings
# ============================================================================

print("\n✅ TEST 3: Fetch All Holdings")
print("-" * 50)

holdings = ["RKLB", "NVDA", "MSFT", "ALAB", "SCHD", "INTC", "NBIS", "NVDL", "SLV", "GRAB"]

print(f"Testing {len(holdings)} stocks...\n")

successful = 0
failed = 0

for ticker in holdings:
    try:
        url = f"{FINNHUB_BASE_URL}/quote"
        params = {"symbol": ticker, "token": FINNHUB_API_KEY}
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if "c" in data and data["c"]:
                price = data["c"]
                change_pct = data.get("dp", 0)
                print(f"✅ {ticker}: ${price:.2f} ({change_pct:+.2f}%)")
                successful += 1
            else:
                print(f"❌ {ticker}: No data")
                failed += 1
        else:
            print(f"❌ {ticker}: HTTP {response.status_code}")
            failed += 1
    except Exception as e:
        print(f"❌ {ticker}: Error - {str(e)[:50]}")
        failed += 1

print(f"\nResult: {successful}/{len(holdings)} successful")

# ============================================================================
# TEST 4: Test Telegram
# ============================================================================

print("\n✅ TEST 4: Telegram Connection")
print("-" * 50)

async def test_telegram():
    try:
        bot = Bot(token=telegram_token)
        chat_id = int(telegram_chat)
        
        print(f"Sending test message to chat ID: {chat_id}")
        
        message = "🧪 **Test Message** - If you see this, Finnhub & Telegram are working!"
        
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown"
        )
        
        print("✅ Test message sent to Telegram!")
        return True
        
    except Exception as e:
        print(f"❌ Telegram Error: {e}")
        return False

telegram_ok = asyncio.run(test_telegram())

# ============================================================================
# SUMMARY
# ============================================================================

print("\n" + "="*50)
print("📊 TEST SUMMARY")
print("="*50)

if successful >= 8:
    print("✅ Finnhub API: WORKING")
else:
    print("❌ Finnhub API: ISSUES")

if telegram_ok:
    print("✅ Telegram Bot: WORKING")
else:
    print("❌ Telegram Bot: ISSUES")

if successful >= 8 and telegram_ok:
    print("\n🎉 Everything is working! Bot should be sending messages!")
else:
    print("\n⚠️ There are issues to fix")

print("="*50)
