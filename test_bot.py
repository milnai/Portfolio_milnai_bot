#!/usr/bin/env python3
"""
Quick test — fires one of each message type immediately.
Run this locally: python test_bot.py
"""

import subprocess, sys

# Auto-install if needed
for pkg in ["yfinance", "requests", "numpy", "APScheduler", "python-telegram-bot", "python-dotenv", "pytz"]:
    try:
        __import__(pkg.lower().replace("-", "_"))
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

import asyncio
from dotenv import load_dotenv
load_dotenv()

# Import everything from your bot
from market_bot import (
    get_vix,
    get_portfolio_data,
    get_market_metrics,
    run_option_hunter,
    format_market_report,
    format_trading_signals,
    format_option_hunter,
    send_telegram,
)

print("=" * 50)
print("🔧 TRADING BOT MESSAGE TEST")
print("=" * 50)

# ── Test 1: Market Report ──────────────────────────
print("\n[1/3] Sending Market Report...")
try:
    vix       = get_vix()
    portfolio = get_portfolio_data()
    market    = get_market_metrics()
    msg       = format_market_report(portfolio, market, vix)
    send_telegram(msg)
    print("✅ Market Report sent!")
    print(f"   VIX: {vix['level']:.2f}" if vix else "   VIX: unavailable")
    print(f"   Positions fetched: {len(portfolio.get('positions', {}))}/{len(portfolio.get('positions', {}))}")
except Exception as e:
    print(f"❌ Market Report failed: {e}")

# ── Test 2: Trading Signals ────────────────────────
print("\n[2/3] Sending Trading Signals...")
try:
    vix = get_vix()
    msg = format_trading_signals(vix)
    send_telegram(msg)
    print("✅ Trading Signals sent!")
except Exception as e:
    print(f"❌ Trading Signals failed: {e}")

# ── Test 3: Option Hunter (scans 5 tickers only for speed) ──
print("\n[3/3] Sending Option Hunter (quick scan: 5 tickers)...")
try:
    import market_bot
    original = market_bot.OPTION_HUNT_TICKERS
    market_bot.OPTION_HUNT_TICKERS = ["SPY", "AAPL", "NVDA", "TSLA", "AMD"]  # Small scan for test

    vix  = get_vix()
    opps = run_option_hunter(vix)
    msg  = format_option_hunter(opps, vix)
    send_telegram(msg)
    print(f"✅ Option Hunter sent! ({len(opps)} setup(s) found)")

    market_bot.OPTION_HUNT_TICKERS = original  # Restore
except Exception as e:
    print(f"❌ Option Hunter failed: {e}")

print("\n" + "=" * 50)
print("✅ All tests complete — check your Telegram!")
print("=" * 50)
