#!/usr/bin/env python3
"""
Quick test — fires one of each message type immediately.
Run: python test_bot.py
"""

import subprocess, sys, traceback

for pkg in ["yfinance", "requests", "numpy", "APScheduler",
            "python-telegram-bot", "python-dotenv", "pytz"]:
    try:
        __import__(pkg.lower().replace("-", "_"))
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

from dotenv import load_dotenv
load_dotenv()

import market_bot
from market_bot import (
    get_vix,
    get_portfolio_data,
    get_market_metrics,
    run_option_hunter,
    run_swing_scanner,
    format_market_report,
    format_trading_signals,
    format_option_hunter,
    format_swing_trades,
    send_telegram,
)

print("=" * 50)
print("🔧 TRADING BOT MESSAGE TEST")
print("=" * 50)

# ── Test 1: Market Report ──────────────────────────
print("\n[1/4] Sending Market Report...")
try:
    vix       = get_vix()
    portfolio = get_portfolio_data()
    market_m  = get_market_metrics()
    msg       = format_market_report(portfolio, market_m, vix)
    send_telegram(msg)
    print(f"✅ Market Report sent!")
    print(f"   VIX: {vix['level']:.2f}" if vix else "   VIX: unavailable")
    print(f"   Positions: {len(portfolio.get('positions', {}))}")
except Exception as e:
    print(f"❌ Market Report failed: {e}")
    traceback.print_exc()

# ── Test 2: Trading Signals ────────────────────────
print("\n[2/4] Sending Trading Signals...")
try:
    vix = get_vix()
    msg = format_trading_signals(vix)
    send_telegram(msg)
    print("✅ Trading Signals sent!")
except Exception as e:
    print(f"❌ Trading Signals failed: {e}")
    traceback.print_exc()

# ── Test 3: Option Hunter (5 tickers for speed) ───
print("\n[3/4] Sending Option Hunter (5 tickers)...")
try:
    original = market_bot.OPTION_HUNT_TICKERS
    market_bot.OPTION_HUNT_TICKERS = ["SPY", "AAPL", "NVDA", "TSLA", "AMD"]

    vix  = get_vix()
    opps = run_option_hunter(vix)
    send_telegram(format_option_hunter(opps, vix))
    print(f"✅ Option Hunter sent! ({len(opps)} setup(s))")

    market_bot.OPTION_HUNT_TICKERS = original
except Exception as e:
    print(f"❌ Option Hunter failed: {e}")
    traceback.print_exc()

# ── Test 4: Swing Trades (5 tickers for speed) ────
print("\n[4/4] Sending Swing Trades (5 tickers)...")
try:
    original = market_bot.OPTION_HUNT_TICKERS
    market_bot.OPTION_HUNT_TICKERS = ["SPY", "AAPL", "NVDA", "TSLA", "AMD"]

    vix    = get_vix()
    setups = run_swing_scanner(vix)
    send_telegram(format_swing_trades(setups, vix))
    print(f"✅ Swing Trades sent! ({len(setups)} setup(s))")

    market_bot.OPTION_HUNT_TICKERS = original
except Exception as e:
    print(f"❌ Swing Trades failed: {e}")
    traceback.print_exc()  # Full error trace so we can fix it instantly

print("\n" + "=" * 50)
print("✅ All tests complete — check Telegram!")
print("=" * 50)