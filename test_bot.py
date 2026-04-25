#!/usr/bin/env python3
"""
Quick test — fires one of each message type immediately.
Run: python test_bot.py
Updated for v3.1 (no Option Hunter, no Swing Trades)
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
    get_portfolio_pnl,
    get_market_metrics,
    run_portfolio_signals,
    run_watchlist_signals,
    run_watchlist_options,
    format_market_report,
    format_portfolio_signals,
    format_eod_summary,
    send_telegram,
)

print("=" * 55)
print("🔧 TRADING BOT v3.1 MESSAGE TEST")
print("=" * 55)

# Test 1: Market Report
print("\n[1/4] Sending Market Report...")
try:
    vix      = get_vix()
    pnl_data = get_portfolio_pnl()
    market_m = get_market_metrics()
    send_telegram(format_market_report(pnl_data, market_m, vix))
    print(f"✅ Market Report sent! VIX={vix['level']:.2f} | Positions={len(pnl_data.get('positions',{}))}")
except Exception as e:
    print(f"❌ Market Report failed: {e}"); traceback.print_exc()

# Test 2: Full Signal Scan (limit watchlist to 5 for speed)
print("\n[2/4] Sending Signal Scan...")
try:
    vix      = get_vix()
    pnl_data = get_portfolio_pnl()
    sc       = pnl_data.get("stock_cache", {})
    original_wl = list(market_bot.live_watchlist)
    market_bot.live_watchlist = original_wl[:5]
    protection, growth = run_portfolio_signals(vix, sc)
    wl_stocks          = run_watchlist_signals(vix)
    wl_options         = run_watchlist_options(vix)
    market_bot.live_watchlist = original_wl
    send_telegram(format_portfolio_signals(protection, growth, wl_stocks, wl_options, vix))
    print(f"✅ Signal Scan sent! Protection={len(protection)} Growth={len(growth)} WL-stocks={len(wl_stocks)} WL-options={len(wl_options)}")
except Exception as e:
    print(f"❌ Signal Scan failed: {e}"); traceback.print_exc()

# Test 3: EOD Summary
print("\n[3/4] Sending EOD Summary...")
try:
    vix      = get_vix()
    pnl_data = get_portfolio_pnl()
    market_m = get_market_metrics()
    send_telegram(format_eod_summary(pnl_data, market_m, vix))
    print("✅ EOD Summary sent!")
except Exception as e:
    print(f"❌ EOD Summary failed: {e}"); traceback.print_exc()

# Test 4: Analyze logic
print("\n[4/4] Testing /analyze on RKLB...")
try:
    from market_bot import get_stock_data, score_ticker, score_to_confidence, classify_signal
    data   = get_stock_data("RKLB")
    scored = score_ticker(data)
    conf   = score_to_confidence(scored["score"])
    print(f"✅ RKLB: ${data['current_price']:.2f} | Score={scored['score']:+d}/6 | Conf={conf:.0%} | {classify_signal(scored['score'])}")
except Exception as e:
    print(f"❌ Analyze failed: {e}"); traceback.print_exc()

print("\n" + "=" * 55)
print("✅ All tests complete — check Telegram!")
print("=" * 55)
