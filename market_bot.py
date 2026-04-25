#!/usr/bin/env python3
"""
Trading Bot v3.1 — Portfolio + Options + Watchlist + Signal Quality
====================================================================
INFRASTRUCTURE: Unchanged (Railway + Telegram + yfinance + APScheduler)

SIGNAL SCAN (one message, 4 blocks):
  🛡️ Portfolio Protection   — your stocks showing bearish/sell signals
  🚀 Portfolio Growth        — your stocks showing bullish/add signals
  🔍 Watchlist Opportunity   — strong stock entries from your watchlist (score ≥5)
  🎯 Watchlist Options       — call/put setups on your watchlist tickers only

REMOVED in v3.1:
  - Option Hunter (broad 64-ticker scan — too many false put signals)
  - Swing Trade Scanner (removed per user preference)

SCHEDULE (all EST, Mon-Fri only):
  Pre-market  : Every 30min 6:30am–11:30am | Signal scan
  Market hours: Every 60min 9:30am–3:30pm  | Market report + signal scan
  Post-market : EOD summary at 4:15pm | Signal scan every 30min 4:00–6:00pm
"""

import subprocess
import sys

_REQUIRED = [
    "yfinance", "requests", "numpy", "APScheduler",
    "python-telegram-bot", "python-dotenv", "pytz",
]

def _ensure_packages():
    for pkg in _REQUIRED:
        try:
            __import__(pkg.lower().replace("-", "_"))
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

_ensure_packages()

import asyncio
import os
import logging
import time
import pytz
import json
import numpy as np
import yfinance as yf
import requests

from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT   = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
FINNHUB_KEY     = os.getenv("FINNHUB_API_KEY", "")

EST = pytz.timezone("US/Eastern")
SGT = pytz.timezone("Asia/Singapore")

# --- Signal thresholds ---
BUY_STRONG  =  5
BUY_MEDIUM  =  4
SELL_WEAK   = -2
SELL_STRONG = -4
MIN_CONFIDENCE = 0.60   # Minimum confidence to send any alert
WATCHLIST_MAX_PER_CYCLE = 3  # Max watchlist alerts per scan cycle

# --- Cooldown: don't re-alert same ticker within this window ---
ALERT_COOLDOWN_MINUTES = 60

# --- Stop loss ---
STOP_LOSS_PCT = 0.035

# --- Options filters ---
OPT_MIN_OI  = 500
OPT_MIN_DTE = 21
OPT_MAX_DTE = 35
OPT_MAX_IV  = 50.0
OPT_OTM_MIN = 0.01
OPT_OTM_MAX = 0.10

# ============================================================================
# PORTFOLIO DATABASE — Single Source of Truth
# Supports both STOCK and OPTION positions
# ============================================================================
"""
Schema:
  portfolio.json = {
    "RKLB_stock": {
      "asset_type": "stock",          # "stock" | "call" | "put"
      "ticker": "RKLB",
      "shares": 67,
      "avg_cost": 68.439,
      "entry_date": "2025-01-15",
      # options-only fields (null for stocks):
      "strike": null,
      "expiry": null,
      "contracts": null
    },
    "NVDA_call_200_2026-06-18": {
      "asset_type": "call",
      "ticker": "NVDA",
      "shares": null,
      "avg_cost": 12.00,              # premium paid per share (×100 = contract cost)
      "entry_date": "2026-04-24",
      "strike": 200.0,
      "expiry": "2026-06-18",
      "contracts": 1
    }
  }
"""

PORTFOLIO_FILE  = "portfolio.json"
WATCHLIST_FILE  = "watchlist.json"
SETTINGS_FILE   = "settings.json"
REMINDERS_FILE  = "reminders.json"
ALERT_STATE_FILE = "alert_state.json"   # Tracks last alert per ticker for dedup

# --- Default portfolio (Apr 24 2026) ---
DEFAULT_PORTFOLIO = {
    "RKLB_stock":            {"asset_type": "stock",  "ticker": "RKLB",  "shares": 67,  "avg_cost": 68.439,  "entry_date": "2025-01-01", "strike": None, "expiry": None, "contracts": None},
    "NVDA_stock":            {"asset_type": "stock",  "ticker": "NVDA",  "shares": 10,  "avg_cost": 175.90,  "entry_date": "2025-01-01", "strike": None, "expiry": None, "contracts": None},
    "NVDA_call_200_20260618":{"asset_type": "call",   "ticker": "NVDA",  "shares": None,"avg_cost": 12.00,   "entry_date": "2026-04-24", "strike": 200.0,"expiry": "2026-06-18","contracts": 1},
    "ALAB_stock":            {"asset_type": "stock",  "ticker": "ALAB",  "shares": 2,   "avg_cost": 116.00,  "entry_date": "2025-01-01", "strike": None, "expiry": None, "contracts": None},
    "NVDL_stock":            {"asset_type": "stock",  "ticker": "NVDL",  "shares": 10,  "avg_cost": 80.90,   "entry_date": "2025-01-01", "strike": None, "expiry": None, "contracts": None},
    "AMZN_stock":            {"asset_type": "stock",  "ticker": "AMZN",  "shares": 2,   "avg_cost": 246.99,  "entry_date": "2026-04-24", "strike": None, "expiry": None, "contracts": None},
    "AMZN_call_270_20260515":{"asset_type": "call",   "ticker": "AMZN",  "shares": None,"avg_cost": 5.65,    "entry_date": "2026-04-24", "strike": 270.0,"expiry": "2026-05-15","contracts": 1},
    "META_call_715_20260508": {"asset_type": "call",  "ticker": "META",  "shares": None,"avg_cost": 8.85,    "entry_date": "2026-04-24", "strike": 715.0,"expiry": "2026-05-08","contracts": 1},
    "MSFT_stock":            {"asset_type": "stock",  "ticker": "MSFT",  "shares": 2,   "avg_cost": 372.50,  "entry_date": "2025-01-01", "strike": None, "expiry": None, "contracts": None},
    "NBIS_stock":            {"asset_type": "stock",  "ticker": "NBIS",  "shares": 2,   "avg_cost": 114.90,  "entry_date": "2025-01-01", "strike": None, "expiry": None, "contracts": None},
    "SCHD_stock":            {"asset_type": "stock",  "ticker": "SCHD",  "shares": 15,  "avg_cost": 30.50,   "entry_date": "2025-01-01", "strike": None, "expiry": None, "contracts": None},
    "TSLA_stock":            {"asset_type": "stock",  "ticker": "TSLA",  "shares": 4,   "avg_cost": 375.00,  "entry_date": "2026-04-24", "strike": None, "expiry": None, "contracts": None},
    "PANW_stock":            {"asset_type": "stock",  "ticker": "PANW",  "shares": 2,   "avg_cost": 175.00,  "entry_date": "2026-04-24", "strike": None, "expiry": None, "contracts": None},
    "ASTS_stock":            {"asset_type": "stock",  "ticker": "ASTS",  "shares": 5,   "avg_cost": 79.80,   "entry_date": "2026-04-24", "strike": None, "expiry": None, "contracts": None},
    "GE_stock":              {"asset_type": "stock",  "ticker": "GE",    "shares": 3,   "avg_cost": 285.33,  "entry_date": "2026-04-24", "strike": None, "expiry": None, "contracts": None},
    "GOOGL_stock":           {"asset_type": "stock",  "ticker": "GOOGL", "shares": 2,   "avg_cost": 337.50,  "entry_date": "2026-04-24", "strike": None, "expiry": None, "contracts": None},
    "GOOGL_call_340_20260515":{"asset_type": "call",  "ticker": "GOOGL", "shares": None,"avg_cost": 13.15,   "entry_date": "2026-04-24", "strike": 340.0,"expiry": "2026-05-15","contracts": 1},
    "GRAB_stock":            {"asset_type": "stock",  "ticker": "GRAB",  "shares": 284, "avg_cost": 5.899,   "entry_date": "2025-01-01", "strike": None, "expiry": None, "contracts": None},
}

DEFAULT_WATCHLIST = [
    # Large-Cap Tech
    "AAPL", "GOOG", "TSM", "AMAT", "ARM", "ASML", "AMD", "MU", "LRCX",
    # Semiconductors & AI Infrastructure
    "MRVL", "GLW", "INTC",
    # Cloud & Software
    "NOW", "PATH", "CRWD", "PANW", "PLTR", "SHOP",
    # China & Asia Tech
    "BABA", "FUTU",
    # Growth & Emerging Tech
    "BBAI", "HIMS", "SOFI", "AXON",
    # Space & Defense
    "LUNR", "RDW", "PL",
    # Commodities & Materials
    "BHP", "SCCO", "XOM", "ZIM",
    # Crypto & Digital Assets
    "HIVE",
    # Niche / Speculative
    "APLD", "NFLX",
    # ETFs
    "GLD", "SLV", "VTI",
]

EARNINGS_CALENDAR = {
    "PLTR":  "2026-05-04",
    "RKLB":  "2026-05-13",
    "AMD":   "2026-05-05",
    "NVDA":  "2026-05-28",
    "AXON":  "2026-05-06",
    "CRWV":  "2026-05-14",
    "NBIS":  "2026-05-12",
    "HIMS":  "2026-05-05",
    "TSLA":  "2026-07-22",
    "NFLX":  "2026-07-16",
    "META":  "2026-04-30",
    "AMZN":  "2026-05-01",
    "AAPL":  "2026-05-01",
    "MSFT":  "2026-04-30",
    "GOOGL": "2026-04-29",
    "PANW":  "2026-05-20",
    "ASTS":  "2026-05-08",
    "GE":    "2026-04-22",
}


# ============================================================================
# PERSISTENCE LAYER
# ============================================================================

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load {path}: {e}")
    return default() if callable(default) else default

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Could not save {path}: {e}")

def load_portfolio():
    data = load_json(PORTFOLIO_FILE, None)
    if data:
        logger.info(f"✅ Portfolio loaded ({len(data)} positions)")
        return data
    logger.info("📋 Using default portfolio")
    return {k: dict(v) for k, v in DEFAULT_PORTFOLIO.items()}

def save_portfolio(p):
    save_json(PORTFOLIO_FILE, p)

def load_watchlist():
    data = load_json(WATCHLIST_FILE, None)
    if data is not None:
        return data
    return list(DEFAULT_WATCHLIST)

def save_watchlist(w):
    save_json(WATCHLIST_FILE, w)

def load_settings():
    return load_json(SETTINGS_FILE, {"swing_capital": 2000, "name": ""})

def save_settings(s):
    save_json(SETTINGS_FILE, s)

def load_reminders():
    return load_json(REMINDERS_FILE, [])

def save_reminders(r):
    save_json(REMINDERS_FILE, r)

def load_alert_state():
    return load_json(ALERT_STATE_FILE, {})

def save_alert_state(s):
    save_json(ALERT_STATE_FILE, s)

# Live state
live_portfolio = load_portfolio()
live_watchlist = load_watchlist()
user_settings  = load_settings()
reminders      = load_reminders()
alert_state    = load_alert_state()  # {ticker: last_alert_iso_timestamp}


# ============================================================================
# PORTFOLIO HELPERS
# ============================================================================

def _make_position_key(ticker, asset_type, strike=None, expiry=None):
    """Generate a unique key for a portfolio position."""
    t = ticker.upper()
    if asset_type == "stock":
        return f"{t}_stock"
    elif asset_type in ("call", "put"):
        s = str(int(strike)) if strike else "0"
        e = expiry.replace("-", "") if expiry else "0"
        return f"{t}_{asset_type}_{s}_{e}"
    return f"{t}_{asset_type}"

def get_stock_tickers_from_portfolio():
    """Return unique stock tickers held (for data fetching)."""
    tickers = set()
    for pos in live_portfolio.values():
        tickers.add(pos["ticker"])
    return list(tickers)

def get_option_positions():
    """Return list of option positions (calls and puts)."""
    return {k: v for k, v in live_portfolio.items() if v["asset_type"] in ("call", "put")}

def get_stock_positions():
    """Return list of stock-only positions."""
    return {k: v for k, v in live_portfolio.items() if v["asset_type"] == "stock"}


# ============================================================================
# DATA LAYER
# ============================================================================

def get_stock_data(ticker):
    """Fetch live quote + 6-month OHLCV via yfinance."""
    try:
        stock = yf.Ticker(ticker)
        info  = stock.fast_info
        current_price = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None)
        prev_close    = getattr(info, "previous_close", None) or getattr(info, "regular_market_previous_close", None)

        if not current_price or float(current_price) == 0:
            return None

        current_price = float(current_price)
        prev_close    = float(prev_close) if prev_close else current_price
        daily_change  = current_price - prev_close
        daily_pct     = (daily_change / prev_close * 100) if prev_close else 0

        hist = yf.download(ticker, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if hist.empty:
            return None

        if hasattr(hist.columns, "levels"):
            hist.columns = hist.columns.get_level_values(0)

        return {
            "ticker":           ticker,
            "current_price":    current_price,
            "prev_close":       prev_close,
            "daily_change":     daily_change,
            "daily_change_pct": daily_pct,
            "closes":           [float(x) for x in hist["Close"].dropna().tolist()],
            "highs":            [float(x) for x in hist["High"].dropna().tolist()],
            "lows":             [float(x) for x in hist["Low"].dropna().tolist()],
            "volumes":          [float(x) for x in hist["Volume"].dropna().tolist()],
        }
    except Exception as e:
        logger.error(f"get_stock_data {ticker}: {e}")
        return None


def get_vix():
    """Fetch VIX and classify sentiment."""
    try:
        info = yf.Ticker("^VIX").fast_info
        vix  = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None)
        if not vix:
            return None
        vix = float(vix)

        if vix < 15:
            sentiment, emoji = "😌 Calm — cheap premiums, calls preferred", "🟢"
        elif vix < 20:
            sentiment, emoji = "😐 Normal — standard conditions", "🟡"
        elif vix < 25:
            sentiment, emoji = "😟 Nervous — size down on all trades", "🟠"
        elif vix < 35:
            sentiment, emoji = "😰 Fearful — strong put / dip-buy zone", "🔴"
        else:
            sentiment, emoji = "😱 PANIC — best dip-buy time, tiny sizing", "🔴🔴"

        return {
            "level": vix, "sentiment": sentiment, "emoji": emoji,
            "is_fearful": vix > 25, "call_friendly": vix < 20,
        }
    except Exception as e:
        logger.error(f"VIX error: {e}")
        return None


def get_option_value(pos, stock_price):
    """
    Estimate current option P&L using intrinsic value approximation.
    For display purposes only — real value needs live options chain.
    """
    try:
        strike   = pos["strike"]
        avg_cost = pos["avg_cost"]      # Premium paid per share
        contracts = pos["contracts"] or 1
        expiry   = pos["expiry"]

        if expiry:
            today    = datetime.now(EST).date()
            exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            dte      = (exp_date - today).days
            if dte < 0:
                return {"status": "EXPIRED", "value": 0, "pnl": -(avg_cost * 100 * contracts)}
        else:
            dte = 30

        # Try to fetch live price from yfinance options chain
        ticker_obj = yf.Ticker(pos["ticker"])
        live_premium = None
        try:
            if expiry and expiry in ticker_obj.options:
                chain = ticker_obj.option_chain(expiry)
                df = chain.calls if pos["asset_type"] == "call" else chain.puts
                row = df[abs(df["strike"] - strike) < 0.5]
                if not row.empty:
                    bid  = float(row.iloc[0].get("bid", 0) or 0)
                    ask  = float(row.iloc[0].get("ask", 0) or 0)
                    last = float(row.iloc[0].get("lastPrice", 0) or 0)
                    live_premium = (bid + ask) / 2 if bid and ask else last
        except:
            pass

        if live_premium is not None and live_premium > 0:
            current_value = live_premium * 100 * contracts
            cost_basis    = avg_cost * 100 * contracts
            pnl           = current_value - cost_basis
            pnl_pct       = (pnl / cost_basis * 100) if cost_basis else 0
            return {
                "status":       f"{dte}DTE",
                "value":        current_value,
                "pnl":          pnl,
                "pnl_pct":      pnl_pct,
                "live_premium": live_premium,
            }

        # Fallback: intrinsic only
        if pos["asset_type"] == "call":
            intrinsic = max(0, stock_price - strike)
        else:
            intrinsic = max(0, strike - stock_price)

        est_value = (intrinsic * 0.7 + avg_cost * 0.3) * 100 * contracts
        cost_basis = avg_cost * 100 * contracts
        pnl = est_value - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0
        return {
            "status":  f"{dte}DTE (est.)",
            "value":   est_value,
            "pnl":     pnl,
            "pnl_pct": pnl_pct,
            "live_premium": None,
        }
    except Exception as e:
        logger.warning(f"get_option_value: {e}")
        return None


# ============================================================================
# CATALYST DETECTION — News via Finnhub
# ============================================================================

def get_news_catalyst(ticker):
    """
    Fetch recent news for ticker via Finnhub.
    Returns catalyst summary, impact (bullish/bearish/neutral), and suggested action.
    """
    if not FINNHUB_KEY:
        return None
    try:
        today    = datetime.now(EST)
        from_dt  = (today - timedelta(days=2)).strftime("%Y-%m-%d")
        to_dt    = today.strftime("%Y-%m-%d")
        url      = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={from_dt}&to={to_dt}&token={FINNHUB_KEY}"
        resp     = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return None
        articles = resp.json()[:5]  # Latest 5
        if not articles:
            return None

        # Simple keyword-based sentiment on headlines
        bullish_kw = ["beat", "beats", "upgrade", "raises guidance", "record", "surge", "strong",
                      "better than expected", "partnership", "contract", "bullish", "buy"]
        bearish_kw = ["miss", "misses", "downgrade", "cut", "layoff", "weak", "below", "warning",
                      "investigation", "lawsuit", "recall", "bearish", "sell"]

        bull_count = bear_count = 0
        headlines  = []
        for art in articles:
            h = art.get("headline", "").lower()
            headlines.append(art.get("headline", ""))
            bull_count += sum(1 for kw in bullish_kw if kw in h)
            bear_count += sum(1 for kw in bearish_kw if kw in h)

        if bull_count > bear_count and bull_count > 0:
            impact  = "🟢 Bullish"
            action  = "Consider adding / holding"
        elif bear_count > bull_count and bear_count > 0:
            impact  = "🔴 Bearish"
            action  = "Consider reducing / protecting"
        else:
            impact  = "⚪ Neutral"
            action  = "No action indicated"

        return {
            "headlines": headlines[:3],
            "impact":    impact,
            "action":    action,
            "bull_count": bull_count,
            "bear_count": bear_count,
        }
    except Exception as e:
        logger.warning(f"News catalyst {ticker}: {e}")
        return None


def _is_volume_spike(data):
    """Returns True if today's volume is >1.5× 20-day average."""
    try:
        vols = data.get("volumes", [])
        if len(vols) < 21:
            return False
        avg_vol   = np.mean(vols[-21:-1])
        vol_ratio = vols[-1] / avg_vol if avg_vol > 0 else 1.0
        return vol_ratio >= 1.5, round(vol_ratio, 1)
    except:
        return False, 1.0


def check_earnings_alerts():
    """Scan earnings calendar for stocks reporting in 1-2 days."""
    alerts = []
    today  = datetime.now(EST).date()
    for ticker, date_str in EARNINGS_CALENDAR.items():
        try:
            earn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            days_away = (earn_date - today).days
            if days_away < 0 or days_away > 2:
                continue
            data   = get_stock_data(ticker)
            score  = 0
            trend  = "neutral"
            if data:
                scored = score_ticker(data)
                if scored:
                    score = scored["score"]
                    trend = "bullish" if score >= 2 else ("bearish" if score <= -2 else "neutral")
            bias = (
                "🟢 CALL candidate" if trend == "bullish" else
                "🔴 PUT candidate"  if trend == "bearish" else
                "⚪ NEUTRAL — high risk"
            )
            reason = (
                "Bullish momentum into earnings — CALL if IV not too high" if trend == "bullish" else
                "Bearish into earnings — protective PUT" if trend == "bearish" else
                "Mixed signals — avoid options on this binary event"
            )
            alerts.append({
                "ticker": ticker, "earn_date": date_str,
                "days_away": days_away, "bias": bias,
                "reason": reason, "score": score,
            })
        except Exception as e:
            logger.warning(f"Earnings check {ticker}: {e}")
    return alerts


# ============================================================================
# TECHNICAL INDICATORS
# ============================================================================

def _ema(closes, period):
    try:
        arr = np.array(closes, dtype=float)
        if len(arr) < period:
            return None
        k, ema = 2.0 / (period + 1), arr[0]
        for p in arr[1:]:
            ema = p * k + ema * (1 - k)
        return ema
    except:
        return None

def _rsi(closes, period=14):
    try:
        arr    = np.array(closes[-(period + 1):], dtype=float)
        if len(arr) < period + 1:
            return None
        deltas   = np.diff(arr)
        avg_gain = np.mean(np.where(deltas > 0, deltas, 0))
        avg_loss = np.mean(np.where(deltas < 0, -deltas, 0))
        return 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    except:
        return None

def _macd(closes, fast=12, slow=26, signal=9):
    try:
        if len(closes) < slow + signal:
            return None, None
        macd_vals = []
        for i in range(slow, len(closes) + 1):
            ef = _ema(closes[:i], fast)
            es = _ema(closes[:i], slow)
            if ef and es:
                macd_vals.append(ef - es)
        if len(macd_vals) < signal:
            return None, None
        return macd_vals[-1], float(np.mean(macd_vals[-signal:]))
    except:
        return None, None

def _bollinger(closes, period=20, std_devs=2):
    try:
        arr = np.array(closes[-period:], dtype=float)
        if len(arr) < period:
            return None
        sma, std = np.mean(arr), np.std(arr)
        return {"upper": sma + std_devs * std, "middle": sma, "lower": sma - std_devs * std}
    except:
        return None

def _adx(highs, lows, closes, period=14):
    try:
        h, l, c = np.array(highs, dtype=float), np.array(lows, dtype=float), np.array(closes, dtype=float)
        if len(c) < period + 2:
            return None
        trs, plus_dms, minus_dms = [], [], []
        for i in range(1, len(c)):
            trs.append(max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])))
            up, down = h[i] - h[i-1], l[i-1] - l[i]
            plus_dms.append(up   if up   > down and up   > 0 else 0)
            minus_dms.append(down if down > up   and down > 0 else 0)
        atr = np.mean(trs[-period:])
        if atr == 0:
            return None
        plus_di  = 100 * np.mean(plus_dms[-period:]) / atr
        minus_di = 100 * np.mean(minus_dms[-period:]) / atr
        denom    = plus_di + minus_di
        return 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0
    except:
        return None

def _atr(highs, lows, closes, period=14):
    try:
        h = np.array(highs[-period-1:], dtype=float)
        l = np.array(lows[-period-1:],  dtype=float)
        c = np.array(closes[-period-1:], dtype=float)
        trs = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, len(c))]
        return np.mean(trs) if trs else None
    except:
        return None


# ============================================================================
# 6-INDICATOR SCORING ENGINE
# ============================================================================

def score_ticker(data):
    try:
        closes, highs, lows, volumes = data["closes"], data["highs"], data["lows"], data["volumes"]
        price, pct = data["current_price"], data["daily_change_pct"]
        score, details = 0, []

        ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
        if ema20 and ema50:
            if ema20 > ema50:
                score += 1; details.append(f"✅ EMA20 ${ema20:.2f} > EMA50 ${ema50:.2f} (uptrend)")
            elif ema20 < ema50:
                score -= 1; details.append(f"❌ EMA20 ${ema20:.2f} < EMA50 ${ema50:.2f} (downtrend)")

        rsi = _rsi(closes)
        if rsi is not None:
            if rsi < 30:
                score += 1; details.append(f"✅ RSI {rsi:.1f} — oversold (bounce likely)")
            elif rsi > 70:
                score -= 1; details.append(f"❌ RSI {rsi:.1f} — overbought (reversal risk)")
            else:
                details.append(f"➖ RSI {rsi:.1f} — neutral")

        macd_line, signal_line = _macd(closes)
        if macd_line is not None and signal_line is not None:
            if macd_line > signal_line:
                score += 1; details.append(f"✅ MACD {macd_line:.3f} > Signal {signal_line:.3f} (bullish)")
            else:
                score -= 1; details.append(f"❌ MACD {macd_line:.3f} < Signal {signal_line:.3f} (bearish)")

        bb = _bollinger(closes)
        if bb:
            if price <= bb["lower"]:
                score += 1; details.append(f"✅ At BB lower ${bb['lower']:.2f} (support bounce)")
            elif price >= bb["upper"]:
                score -= 1; details.append(f"❌ At BB upper ${bb['upper']:.2f} (resistance)")
            else:
                details.append(f"➖ BB mid-range (${bb['lower']:.2f}—${bb['upper']:.2f})")

        if len(volumes) >= 20:
            avg_vol   = np.mean(volumes[-20:])
            vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
            if vol_ratio >= 1.5:
                if pct >= 0:
                    score += 1; details.append(f"✅ Volume {vol_ratio:.1f}x avg (buying conviction)")
                else:
                    score -= 1; details.append(f"❌ Volume {vol_ratio:.1f}x avg (selling pressure)")
            else:
                details.append(f"➖ Volume {vol_ratio:.1f}x avg (low conviction)")

        adx = _adx(highs, lows, closes)
        if adx is not None:
            if adx > 25:
                adj = 1 if score > 0 else -1
                score += adj; details.append(f"✅ ADX {adx:.1f} — strong trend (confirms direction)")
            else:
                details.append(f"➖ ADX {adx:.1f} — weak/choppy (no confirmation)")

        return {"score": score, "details": details, "rsi": rsi, "bb": bb, "ema20": ema20, "ema50": ema50}
    except Exception as e:
        logger.error(f"score_ticker {data.get('ticker')}: {e}")
        return None


def classify_signal(score):
    if score >= BUY_STRONG:  return "STRONG_BUY"
    if score >= BUY_MEDIUM:  return "MEDIUM_BUY"
    if score <= SELL_STRONG: return "STRONG_SELL"
    if score <= SELL_WEAK:   return "WEAK_SELL"
    return None


def score_to_confidence(score):
    """Map score magnitude to confidence percentage."""
    abs_score = abs(score)
    if abs_score >= 5: return 0.90
    if abs_score >= 4: return 0.75
    if abs_score >= 3: return 0.65
    if abs_score >= 2: return 0.55
    return 0.45


# ============================================================================
# SIGNAL QUALITY CONTROL — Deduplication + Confidence Gate
# ============================================================================

def _should_alert(ticker, signal_type, score):
    """
    Returns True if this ticker/signal should fire.
    Blocks if: confidence < MIN_CONFIDENCE, or same ticker alerted < 1hr ago.
    """
    global alert_state

    confidence = score_to_confidence(score)
    if confidence < MIN_CONFIDENCE:
        return False, confidence

    now = datetime.now(EST)
    last_alert = alert_state.get(ticker)
    if last_alert:
        try:
            last_dt   = datetime.fromisoformat(last_alert).replace(tzinfo=EST)
            elapsed   = (now - last_dt).total_seconds() / 60
            if elapsed < ALERT_COOLDOWN_MINUTES:
                return False, confidence
        except:
            pass

    return True, confidence


def _record_alert(ticker):
    """Mark this ticker as alerted now."""
    global alert_state
    alert_state[ticker] = datetime.now(EST).isoformat()
    save_alert_state(alert_state)


# ============================================================================
# SIGNAL FORMATTERS — Structured alert format per requirements
# ============================================================================

def _format_signal_alert(
    signal_type,    # "PORTFOLIO_PROTECTION" | "PORTFOLIO_GROWTH" | "WATCHLIST_OPPORTUNITY"
    ticker,
    price,
    score,
    confidence,
    what_happened,
    why_it_matters,
    strategy_note,
    suggested_action,
    details,
    catalyst=None,
    earn_warning=None,
):
    """
    Unified alert format per requirements:
      Signal type / Ticker / What / Why / Strategy / Action / Confidence
    """
    labels = {
        "PORTFOLIO_PROTECTION":   "🛡️ PORTFOLIO PROTECTION",
        "PORTFOLIO_GROWTH":       "🚀 PORTFOLIO GROWTH",
        "WATCHLIST_OPPORTUNITY":  "🔍 WATCHLIST OPPORTUNITY",
    }
    signal_labels = {
        "STRONG_BUY":  "🟢🟢 STRONG BUY",
        "MEDIUM_BUY":  "🟢 MEDIUM BUY",
        "STRONG_SELL": "🔴🔴 STRONG SELL",
        "WEAK_SELL":   "🔴 TRIM SIGNAL",
    }
    sig_class = classify_signal(score)
    sig_label = signal_labels.get(sig_class, "⏸️ HOLD")

    conf_bar  = "▓" * int(confidence * 10) + "░" * (10 - int(confidence * 10))

    msg  = f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"{labels.get(signal_type, signal_type)}\n"
    msg += f"{sig_label}: *{ticker}* @ ${price:.2f}\n"
    msg += f"Score: {score:+d}/6 | Confidence: {confidence:.0%} [{conf_bar}]\n\n"
    msg += f"📌 *What happened:* {what_happened}\n"
    msg += f"💡 *Why it matters:* {why_it_matters}\n"
    msg += f"📐 *Strategy:* {strategy_note}\n"
    msg += f"✅ *Action:* {suggested_action}\n"

    if catalyst:
        msg += f"\n📰 *Catalyst:* {catalyst['impact']}\n"
        for h in catalyst["headlines"][:2]:
            msg += f"   • {h[:80]}{'…' if len(h)>80 else ''}\n"

    if earn_warning:
        msg += f"\n⚠️ {earn_warning}\n"

    msg += f"\n*Top signals:*\n"
    for d in details[:4]:
        msg += f"  {d}\n"

    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    return msg


# ============================================================================
# PORTFOLIO DATA & P&L
# ============================================================================

def get_portfolio_pnl():
    """
    Returns full P&L breakdown: stocks + options.
    Also computes daily P&L from prev_close.
    """
    total_cost       = total_value    = 0
    total_daily_pnl  = 0
    positions        = {}
    stock_cache      = {}

    # Pre-fetch all unique stock prices
    for pos in live_portfolio.values():
        tk = pos["ticker"]
        if tk not in stock_cache:
            data = get_stock_data(tk)
            stock_cache[tk] = data

    for key, pos in live_portfolio.items():
        tk   = pos["ticker"]
        data = stock_cache.get(tk)
        if not data:
            continue

        price      = data["current_price"]
        prev_close = data["prev_close"]

        if pos["asset_type"] == "stock":
            shares   = pos["shares"]
            avg_cost = pos["avg_cost"]
            cost     = shares * avg_cost
            value    = shares * price
            pnl      = value - cost
            pnl_pct  = (pnl / cost * 100) if cost > 0 else 0
            daily_pnl = shares * (price - prev_close)

            total_cost      += cost
            total_value     += value
            total_daily_pnl += daily_pnl

            positions[key] = {
                "key": key, "ticker": tk, "asset_type": "stock",
                "shares": shares, "avg_cost": avg_cost,
                "price": price, "cost": cost, "value": value,
                "pnl": pnl, "pnl_pct": pnl_pct, "daily_pnl": daily_pnl,
            }

        elif pos["asset_type"] in ("call", "put"):
            opt = get_option_value(pos, price)
            if not opt or opt["status"] == "EXPIRED":
                # Expired = full loss
                cost = (pos["avg_cost"] * 100 * (pos["contracts"] or 1))
                positions[key] = {
                    "key": key, "ticker": tk, "asset_type": pos["asset_type"],
                    "strike": pos["strike"], "expiry": pos["expiry"],
                    "contracts": pos["contracts"],
                    "price": 0, "cost": cost, "value": 0,
                    "pnl": -cost, "pnl_pct": -100, "daily_pnl": 0,
                    "status": "EXPIRED",
                }
                total_cost  += cost
                total_daily_pnl += 0
            else:
                cost = pos["avg_cost"] * 100 * (pos["contracts"] or 1)
                total_cost      += cost
                total_value     += opt["value"]
                total_daily_pnl += 0   # Options daily P&L hard to compute without prev chain

                positions[key] = {
                    "key": key, "ticker": tk, "asset_type": pos["asset_type"],
                    "strike": pos["strike"], "expiry": pos["expiry"],
                    "contracts": pos["contracts"], "avg_cost": pos["avg_cost"],
                    "price": opt.get("live_premium", pos["avg_cost"]),
                    "cost": cost, "value": opt["value"],
                    "pnl": opt["pnl"], "pnl_pct": opt["pnl_pct"],
                    "daily_pnl": 0, "status": opt["status"],
                }

    return {
        "positions":       positions,
        "total_value":     total_value,
        "total_cost":      total_cost,
        "total_pnl":       total_value - total_cost,
        "total_pnl_pct":   ((total_value - total_cost) / total_cost * 100) if total_cost else 0,
        "daily_pnl":       total_daily_pnl,
        "stock_cache":     stock_cache,
    }


def get_market_metrics():
    metrics = {}
    for ticker in ["SPY", "QQQ"]:
        data = get_stock_data(ticker)
        if data:
            metrics[ticker] = {"current": data["current_price"], "daily_pct": data["daily_change_pct"]}
    return metrics or None


# ============================================================================
# PORTFOLIO SIGNALS — Protection + Growth
# ============================================================================

def run_portfolio_signals(vix, stock_cache=None):
    """
    Scan all portfolio stock positions for Protection and Growth signals.
    Returns (protection_signals, growth_signals)
    """
    protection, growth = [], []
    if stock_cache is None:
        stock_cache = {}

    stock_positions = get_stock_positions()

    for key, pos in stock_positions.items():
        tk = pos["ticker"]
        try:
            data = stock_cache.get(tk) or get_stock_data(tk)
            if not data:
                continue
            stock_cache[tk] = data

            scored = score_ticker(data)
            if not scored:
                continue

            score      = scored["score"]
            price      = data["current_price"]
            avg_cost   = pos["avg_cost"]
            pnl_pct    = ((price - avg_cost) / avg_cost * 100) if avg_cost else 0
            signal     = classify_signal(score)
            confidence = score_to_confidence(score)

            # --- Portfolio Protection: SELL/TRIM signals on held positions ---
            if signal in ("STRONG_SELL", "WEAK_SELL"):
                should, conf = _should_alert(tk, "PORTFOLIO_PROTECTION", score)
                if not should:
                    continue

                what      = f"{tk} showing {abs(score)}/6 bearish indicators"
                why       = f"Technical deterioration — P&L currently {pnl_pct:+.1f}% (${(price-avg_cost)*pos['shares']:+.0f})"
                strategy  = "Staged exit: sell 30-50% to protect gains, hold rest with tighter stop"
                action    = f"Trim to ${scored['bb']['upper']:.2f} (BB upper)" if scored.get('bb') else "Reduce 30-50% of position"
                earn_warn = None
                earn_date = EARNINGS_CALENDAR.get(tk)
                if earn_date:
                    days = (datetime.strptime(earn_date, "%Y-%m-%d").date() - datetime.now(EST).date()).days
                    if 0 <= days <= 5:
                        earn_warn = f"Earnings in {days} days ({earn_date})"

                protection.append({
                    "signal_type": "PORTFOLIO_PROTECTION",
                    "ticker": tk, "price": price, "score": score,
                    "confidence": conf, "what": what, "why": why,
                    "strategy": strategy, "action": action,
                    "details": scored["details"],
                    "earn_warning": earn_warn,
                })
                _record_alert(tk)

            # --- Portfolio Growth: BUY/ADD signals on held positions ---
            elif signal in ("STRONG_BUY", "MEDIUM_BUY"):
                # Only flag if reasonable P&L (not over-extended)
                if pnl_pct > 30 and signal == "MEDIUM_BUY":
                    continue   # Already up 30%, medium signal not strong enough to add

                should, conf = _should_alert(tk, "PORTFOLIO_GROWTH", score)
                if not should:
                    continue

                entry    = scored["bb"]["lower"] if scored.get("bb") else price * 0.99
                stop     = entry * (1 - STOP_LOSS_PCT)
                what     = f"{tk} showing {score}/6 bullish indicators"
                why      = f"Trend and momentum aligning — current P&L {pnl_pct:+.1f}% | adding adds to winner"
                strategy = "Add at BB lower if signal holds; stop at 3.5% below entry"
                action   = f"Add shares near ${entry:.2f} | Stop: ${stop:.2f}"

                growth.append({
                    "signal_type": "PORTFOLIO_GROWTH",
                    "ticker": tk, "price": price, "score": score,
                    "confidence": conf, "what": what, "why": why,
                    "strategy": strategy, "action": action,
                    "details": scored["details"],
                })
                _record_alert(tk)

        except Exception as e:
            logger.error(f"portfolio_signals {tk}: {e}")

    return protection, growth


# ============================================================================
# WATCHLIST SIGNALS — Opportunity Detection
# ============================================================================

def run_watchlist_signals(vix):
    """
    Scan watchlist for high-confidence buy opportunities.
    Capped at WATCHLIST_MAX_PER_CYCLE results.
    Only fires STRONG_BUY (score >= 5) or high-confidence MEDIUM_BUY with catalyst.
    """
    opportunities = []
    count = 0

    for tk in live_watchlist:
        if count >= WATCHLIST_MAX_PER_CYCLE:
            break
        try:
            data = get_stock_data(tk)
            if not data:
                continue

            scored = score_ticker(data)
            if not scored:
                continue

            score  = scored["score"]
            signal = classify_signal(score)

            # Watchlist: only very high confidence signals
            if score < BUY_STRONG:  # Must be 5+ for watchlist
                continue

            # VIX gate
            if vix and vix["level"] > 30:
                continue

            should, conf = _should_alert(tk, "WATCHLIST_OPPORTUNITY", score)
            if not should:
                continue

            price    = data["current_price"]
            bb       = scored.get("bb")
            entry    = bb["lower"] if bb else price * 0.99
            stop     = entry * (1 - STOP_LOSS_PCT)
            target1  = entry * 1.05
            target2  = entry * 1.10
            catalyst = get_news_catalyst(tk)

            earn_warn = None
            earn_date = EARNINGS_CALENDAR.get(tk)
            if earn_date:
                days = (datetime.strptime(earn_date, "%Y-%m-%d").date() - datetime.now(EST).date()).days
                if 0 <= days <= 5:
                    earn_warn = f"⚠️ Earnings in {days} days — SKIP options, stock only"

            what     = f"{tk} scoring {score}/6 — strong technical alignment"
            why      = f"All key indicators bullish: EMA, MACD, and volume confirming"
            strategy = "Watchlist entry: small size (1% capital), add on confirmation"
            action   = f"Buy near ${entry:.2f} | Stop: ${stop:.2f} | T1: ${target1:.2f} | T2: ${target2:.2f}"

            opportunities.append({
                "signal_type": "WATCHLIST_OPPORTUNITY",
                "ticker": tk, "price": price, "score": score,
                "confidence": conf, "what": what, "why": why,
                "strategy": strategy, "action": action,
                "details": scored["details"],
                "catalyst": catalyst,
                "earn_warning": earn_warn,
            })
            _record_alert(tk)
            count += 1

        except Exception as e:
            logger.error(f"watchlist_signals {tk}: {e}")

    return opportunities


# ============================================================================
# WATCHLIST OPTIONS SCANNER
# Scans only your watchlist tickers for call setups (bullish signals only).
# Only fires on STRONG_BUY (score ≥5) to avoid false put recommendations.
# ============================================================================

def _is_near_earnings(ticker):
    today    = datetime.now(EST).date()
    date_str = EARNINGS_CALENDAR.get(ticker)
    if not date_str:
        return False
    earn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    return 0 <= (earn_date - today).days <= 2


def get_best_call(ticker, current_price):
    """
    Fetch the best CALL contract for a watchlist ticker.
    Only CALLs — we don't recommend puts on watchlist tickers since
    you'd only be watching them if you're bullish on them.
    Filters: OI >500, DTE 21–35, IV <60%, strike 1–10% OTM.
    """
    try:
        stock = yf.Ticker(ticker)
        expirations = stock.options
        if not expirations:
            return None

        today = datetime.now().date()
        best_exp, best_diff = None, 9999
        for exp in expirations:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte  = (exp_date - today).days
            diff = abs(dte - 28)
            if OPT_MIN_DTE <= dte <= OPT_MAX_DTE and diff < best_diff:
                best_diff, best_exp = diff, exp

        # Fallback to nearest expiry ≥14 DTE
        if not best_exp:
            for exp in expirations:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                if (exp_date - today).days >= 14:
                    best_exp = exp
                    break
        if not best_exp:
            return None

        chain  = stock.option_chain(best_exp)
        dte    = (datetime.strptime(best_exp, "%Y-%m-%d").date() - today).days
        df     = chain.calls
        strike_min = current_price * (1 + OPT_OTM_MIN)
        strike_max = current_price * (1 + OPT_OTM_MAX)

        filtered = df[
            (df["strike"] >= strike_min) &
            (df["strike"] <= strike_max) &
            (df["openInterest"] >= OPT_MIN_OI)
        ]
        if filtered.empty:
            return None

        best = filtered.sort_values("openInterest", ascending=False).iloc[0]
        iv   = float(best.get("impliedVolatility", 0)) * 100
        if iv > OPT_MAX_IV:
            return None

        bid  = float(best.get("bid",  0) or 0)
        ask  = float(best.get("ask",  0) or 0)
        last = float(best.get("lastPrice", 0) or 0)
        mid  = (bid + ask) / 2 if bid and ask else last
        if mid <= 0:
            return None

        return {
            "strike":  float(best["strike"]),
            "expiry":  best_exp,
            "dte":     dte,
            "mid":     mid,
            "bid":     bid,
            "ask":     ask,
            "oi":      int(best.get("openInterest", 0) or 0),
            "volume":  int(best.get("volume", 0) or 0),
            "iv":      round(iv, 1),
        }
    except Exception as e:
        logger.warning(f"get_best_call {ticker}: {e}")
        return None


def run_watchlist_options(vix):
    """
    Scan watchlist for strong bullish signals, then look for a matching CALL.
    Only fires on score ≥5 (STRONG_BUY) and only recommends CALLs.
    Skips tickers with earnings within 2 days (IV spike risk).
    Capped at 3 results per cycle.
    """
    logger.info("🎯 Watchlist Options scan running...")
    results = []

    # Skip if VIX too high (expensive premiums)
    if vix and vix["level"] > 30:
        logger.info("🎯 Watchlist Options skipped — VIX > 30, premiums too expensive")
        return []

    for ticker in live_watchlist:
        if len(results) >= 3:
            break
        try:
            # Skip earnings risk
            if _is_near_earnings(ticker):
                logger.info(f"   {ticker} skipped — earnings within 2 days")
                continue

            data = get_stock_data(ticker)
            if not data:
                continue

            scored = score_ticker(data)
            if not scored:
                continue

            # Only STRONG_BUY (≥5) for options — medium signals not reliable enough
            if scored["score"] < BUY_STRONG:
                continue

            should, conf = _should_alert(f"{ticker}_option", "WATCHLIST_OPTION", scored["score"])
            if not should:
                continue

            price  = data["current_price"]
            option = get_best_call(ticker, price)
            if not option:
                continue

            catalyst  = get_news_catalyst(ticker)
            earn_warn = None
            earn_date = EARNINGS_CALENDAR.get(ticker)
            if earn_date:
                days = (datetime.strptime(earn_date, "%Y-%m-%d").date() - datetime.now(EST).date()).days
                if 3 <= days <= 14:
                    earn_warn = f"⚠️ Earnings in {days} days — IV may spike, size small"

            results.append({
                "ticker":     ticker,
                "price":      price,
                "score":      scored["score"],
                "confidence": conf,
                "details":    scored["details"],
                "option":     option,
                "catalyst":   catalyst,
                "earn_warning": earn_warn,
            })
            _record_alert(f"{ticker}_option")
            time.sleep(0.5)

        except Exception as e:
            logger.error(f"Watchlist Options {ticker}: {e}")

    logger.info(f"🎯 Watchlist Options found {len(results)} setup(s)")
    return results


# ============================================================================
# MESSAGE FORMATTERS
# ============================================================================

def _time_display():
    now_est = datetime.now(EST)
    now_sgt = now_est.astimezone(SGT)
    return {
        "est": now_est.strftime("%Y-%m-%d %H:%M EST"),
        "sgt": now_sgt.strftime("%Y-%m-%d %H:%M SGT"),
    }


def format_market_report(pnl_data, market, vix):
    try:
        t   = _time_display()
        msg = "🕐 *MARKET SNAPSHOT*\n"
        msg += f"_{t['sgt']} | {t['est']}_\n\n"

        if vix:
            msg += f"{vix['emoji']} *VIX: {vix['level']:.2f}*\n{vix['sentiment']}\n\n"

        msg += "📊 *MARKET INDICES*\n"
        if market:
            for tk, d in market.items():
                e = "🟢" if d["daily_pct"] >= 0 else "🔴"
                msg += f"{e} {tk}: ${d['current']:.2f} ({d['daily_pct']:+.2f}%)\n"

        msg += "\n💼 *PORTFOLIO*\n"
        positions = pnl_data.get("positions", {})
        if positions:
            for key, pos in positions.items():
                e = "🟢" if pos["pnl_pct"] >= 0 else "🔴"
                if pos["asset_type"] == "stock":
                    msg += f"{e} {pos['ticker']}: ${pos['price']:.2f} | {pos['pnl_pct']:+.1f}% (${pos['pnl']:+.0f})\n"
                else:
                    strike_label = f"${pos['strike']:.0f}" if pos.get("strike") else ""
                    etype = pos["asset_type"].upper()
                    msg += f"{e} {pos['ticker']} {strike_label} {etype}: {pos['pnl_pct']:+.1f}% (${pos['pnl']:+.0f}) [{pos.get('status','')}]\n"

            msg += "\n" + "=" * 30 + "\n"
            e = "🟢" if pnl_data["total_pnl_pct"] >= 0 else "🔴"
            daily_e = "🟢" if pnl_data["daily_pnl"] >= 0 else "🔴"
            msg += f"{e} *TOTAL P&L: ${pnl_data['total_pnl']:+.0f} ({pnl_data['total_pnl_pct']:+.1f}%)*\n"
            msg += f"{daily_e} *Today: ${pnl_data['daily_pnl']:+.0f}*\n"
        return msg
    except Exception as e:
        logger.error(f"format_market_report: {e}")
        return "❌ Market report error"


def format_eod_summary(pnl_data, market, vix):
    """End-of-day summary: total value, daily P&L, top gainers/losers."""
    try:
        t   = _time_display()
        msg = "🔔 *END OF DAY SUMMARY*\n"
        msg += f"_{t['sgt']} | {t['est']}_\n\n"

        if vix:
            msg += f"{vix['emoji']} VIX: {vix['level']:.2f} — {vix['sentiment']}\n\n"

        if market:
            msg += "📊 *Market Close:*\n"
            for tk, d in market.items():
                e = "🟢" if d["daily_pct"] >= 0 else "🔴"
                msg += f"{e} {tk}: {d['daily_pct']:+.2f}%\n"
            msg += "\n"

        positions = pnl_data.get("positions", {})
        stock_pos = {k: v for k, v in positions.items() if v["asset_type"] == "stock"}
        opt_pos   = {k: v for k, v in positions.items() if v["asset_type"] in ("call","put")}

        # Portfolio totals
        daily_e   = "🟢" if pnl_data["daily_pnl"] >= 0 else "🔴"
        total_e   = "🟢" if pnl_data["total_pnl"] >= 0 else "🔴"
        msg += f"💼 *Portfolio Value: ${pnl_data['total_value']:,.0f}*\n"
        msg += f"{daily_e} Day P&L: ${pnl_data['daily_pnl']:+.0f}\n"
        msg += f"{total_e} Total P&L: ${pnl_data['total_pnl']:+.0f} ({pnl_data['total_pnl_pct']:+.1f}%)\n\n"

        # Top gainers (stocks, by daily P&L)
        sorted_pos = sorted(stock_pos.values(), key=lambda x: x["daily_pnl"], reverse=True)
        if sorted_pos:
            msg += "🏆 *Top Gainers Today:*\n"
            for p in sorted_pos[:3]:
                if p["daily_pnl"] <= 0:
                    break
                msg += f"🟢 {p['ticker']}: +${p['daily_pnl']:.0f} ({((p['price']-p['prev_close'] if 'prev_close' in p else 0)/p.get('prev_close',p['price'])*100) if p.get('prev_close') else 0:+.1f}%)\n"
            msg += "\n"

            msg += "📉 *Top Losers Today:*\n"
            for p in reversed(sorted_pos[-3:]):
                if p["daily_pnl"] >= 0:
                    break
                msg += f"🔴 {p['ticker']}: ${p['daily_pnl']:.0f}\n"
            msg += "\n"

        # Options snapshot
        if opt_pos:
            msg += "🎯 *Options P&L:*\n"
            for p in opt_pos.values():
                e = "🟢" if p["pnl"] >= 0 else "🔴"
                msg += f"{e} {p['ticker']} ${p.get('strike',0):.0f} {p['asset_type'].upper()} [{p.get('status','')}]: ${p['pnl']:+.0f} ({p['pnl_pct']:+.1f}%)\n"
            msg += "\n"

        msg += "_/portfolio for live detail | /scan for tomorrow's setups_"
        return msg
    except Exception as e:
        logger.error(f"format_eod_summary: {e}")
        return "❌ EOD summary error"


def format_watchlist_options(options_setups, vix):
    """Format the 🎯 Watchlist Options block — called inside format_portfolio_signals."""
    if not options_setups:
        if vix and vix["level"] > 30:
            return "🎯 *WATCHLIST OPTIONS:* Skipped — VIX > 30, premiums too expensive\n\n"
        return "🎯 *WATCHLIST OPTIONS:* No strong call setups on watchlist (score ≥5 required)\n\n"

    msg = f"🎯 *WATCHLIST OPTIONS ({len(options_setups)} setup(s))*\n\n"
    if vix:
        iv_note = "✅ Low IV — cheap premiums" if vix["call_friendly"] else "⚠️ Elevated IV — size smaller"
        msg += f"{vix['emoji']} VIX: {vix['level']:.2f} — {iv_note}\n\n"

    for s in options_setups:
        opt  = s["option"]
        conf = s["confidence"]
        conf_bar = "▓" * int(conf * 10) + "░" * (10 - int(conf * 10))
        max_risk   = round(opt["mid"] * 100, 2)
        target_pnl = round(opt["mid"] * 2 * 100, 2)

        msg += f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🟢🟢 WATCHLIST CALL: *{s['ticker']}* @ ${s['price']:.2f}\n"
        msg += f"Score: {s['score']:+d}/6 | Confidence: {conf:.0%} [{conf_bar}]\n\n"
        msg += f"📌 *Why:* {s['score']}/6 indicators bullish — strong technical alignment\n"
        msg += f"✅ *Action:* Only enter if stock stays ≤ ${s['price'] * 1.005:.2f} (within 0.5%)\n\n"
        msg += f"*Contract:* ${opt['strike']:.0f} CALL | Exp: {opt['expiry']} ({opt['dte']} DTE)\n"
        msg += f"*Premium:* ~${opt['mid']:.2f}/share (~${max_risk:.0f}/contract)\n"
        msg += f"*Target:* ${opt['mid'] * 2:.2f} (2×) = +${target_pnl:.0f} | OI: {opt['oi']:,} | IV: {opt['iv']:.0f}%\n"
        msg += f"*Stop:* Exit if premium drops 50% (−${max_risk * 0.5:.0f})\n\n"

        msg += "*Top signals:*\n"
        for d in s["details"][:4]:
            msg += f"  {d}\n"

        if s.get("catalyst"):
            cat = s["catalyst"]
            msg += f"\n📰 *News:* {cat['impact']}\n"
            for h in cat["headlines"][:1]:
                msg += f"  • {h[:75]}{'…' if len(h) > 75 else ''}\n"

        if s.get("earn_warning"):
            msg += f"\n{s['earn_warning']}\n"

        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    msg += "⚠️ _1 contract = 100 shares. Max 2% capital. Options can expire worthless._\n\n"
    return msg


def format_portfolio_signals(protection, growth, watchlist_stocks, watchlist_options, vix):
    """
    Unified signal scan — 4 blocks:
      🛡️ Portfolio Protection   (your stocks, bearish signals)
      🚀 Portfolio Growth        (your stocks, bullish add signals)
      🔍 Watchlist Opportunity   (watchlist stocks, score ≥5)
      🎯 Watchlist Options       (watchlist CALLs only, score ≥5)
    """
    try:
        t   = _time_display()
        msg = f"📡 *SIGNAL SCAN*\n_{t['sgt']} | {t['est']}_\n\n"

        if vix:
            msg += f"{vix['emoji']} VIX: {vix['level']:.2f} — {vix['sentiment']}\n\n"

        # ── Block 1: Portfolio Protection ─────────────────────────────
        if protection:
            msg += f"🛡️ *PORTFOLIO PROTECTION ({len(protection)} alert(s))*\n\n"
            for s in protection:
                msg += _format_signal_alert(
                    s["signal_type"], s["ticker"], s["price"], s["score"],
                    s["confidence"], s["what"], s["why"], s["strategy"], s["action"],
                    s["details"], earn_warning=s.get("earn_warning"),
                ) + "\n\n"
        else:
            msg += "🛡️ *PORTFOLIO PROTECTION:* All positions healthy — no trim signals\n\n"

        # ── Block 2: Portfolio Growth ──────────────────────────────────
        if growth:
            msg += f"🚀 *PORTFOLIO GROWTH ({len(growth)} signal(s))*\n\n"
            for s in growth:
                msg += _format_signal_alert(
                    s["signal_type"], s["ticker"], s["price"], s["score"],
                    s["confidence"], s["what"], s["why"], s["strategy"], s["action"],
                    s["details"],
                ) + "\n\n"
        else:
            msg += "🚀 *PORTFOLIO GROWTH:* No add signals this cycle\n\n"

        # ── Block 3: Watchlist Stock Opportunities ─────────────────────
        if watchlist_stocks:
            msg += f"🔍 *WATCHLIST OPPORTUNITIES ({len(watchlist_stocks)} found)*\n\n"
            for s in watchlist_stocks:
                msg += _format_signal_alert(
                    s["signal_type"], s["ticker"], s["price"], s["score"],
                    s["confidence"], s["what"], s["why"], s["strategy"], s["action"],
                    s["details"], catalyst=s.get("catalyst"),
                    earn_warning=s.get("earn_warning"),
                ) + "\n\n"
        else:
            msg += "🔍 *WATCHLIST STOCKS:* No high-confidence entries (score ≥5 required)\n\n"

        # ── Block 4: Watchlist Options ─────────────────────────────────
        msg += format_watchlist_options(watchlist_options, vix)

        msg += "_Confidence ≥60% | Cooldown: 60min/ticker | Max 3 watchlist alerts/cycle_"
        return msg
    except Exception as e:
        logger.error(f"format_portfolio_signals: {e}")
        return "❌ Signal format error"


def format_earnings_alerts(alerts):
    if not alerts:
        return None
    t   = _time_display()
    msg = "📅 *EARNINGS ALERT — OPTIONS WATCH*\n"
    msg += f"_{t['sgt']} | {t['est']}_\n\n"
    for a in alerts:
        day_label = "TODAY after close" if a["days_away"] == 0 else ("TOMORROW" if a["days_away"] == 1 else "In 2 days")
        msg += f"{a['bias']}: *{a['ticker']}*\n"
        msg += f"Earnings: {a['earn_date']} ({day_label})\n"
        msg += f"Tech score: {a['score']:+d}/6 | {a['reason']}\n"
        msg += f"⚠️ IV spikes at open — buy options BEFORE report\n\n"
    msg += "_Earnings = binary risk. Max 1% capital per trade._"
    return msg


def format_reminders_section():
    if not reminders:
        return None
    today = datetime.now(EST).date()
    t     = _time_display()
    lines = []
    to_remove = []
    for r in reminders:
        ticker = r["ticker"]
        action = r["action"]
        note   = r.get("note", "")
        emoji  = "🟢" if action == "BUY" else ("🔴" if action == "SELL" else "⏸️")
        if r.get("remind_date"):
            try:
                remind_dt = datetime.strptime(r["remind_date"], "%Y-%m-%d").date()
                days_left = (remind_dt - today).days
                if days_left < 0:
                    to_remove.append(r); continue
                elif days_left == 0:
                    lines.append(f"🔔 *TODAY* — {emoji} {action} *{ticker}*\n   {note}")
                    to_remove.append(r)
                elif days_left <= 3:
                    lines.append(f"⏰ *In {days_left} day(s)* — {emoji} {action} *{ticker}*\n   {note}")
                else:
                    lines.append(f"📅 *{r['remind_date']}* — {emoji} {action} *{ticker}*\n   {note}")
            except:
                continue
        elif r.get("target_price"):
            try:
                target  = float(r["target_price"])
                data    = get_stock_data(ticker)
                current = data["current_price"] if data else None
                if current:
                    diff_pct = ((current - target) / target * 100)
                    if action == "BUY" and current <= target * 1.005:
                        lines.append(f"🔔 *PRICE HIT!* 🟢 BUY *{ticker}* @ ${current:.2f} (target ${target:.2f})\n   {note}")
                        to_remove.append(r)
                    elif action == "SELL" and current >= target * 0.995:
                        lines.append(f"🔔 *PRICE HIT!* 🔴 SELL *{ticker}* @ ${current:.2f} (target ${target:.2f})\n   {note}")
                        to_remove.append(r)
                    else:
                        arrow = "📈" if action == "BUY" else "📉"
                        lines.append(f"{arrow} {emoji} {action} *{ticker}* when ${target:.2f} | Now ${current:.2f} ({diff_pct:+.1f}% away)\n   {note}")
            except:
                continue
    for r in to_remove:
        if r in reminders:
            reminders.remove(r)
    if to_remove:
        save_reminders(reminders)
    if not lines:
        return None
    msg  = "🔔 *REMINDERS*\n"
    msg += f"_{t['sgt']} | {t['est']}_\n\n"
    msg += "\n".join(lines)
    msg += "\n\n_/reminders to manage | /remind TICKER to add_"
    return msg


# ============================================================================
# TELEGRAM SENDER
# ============================================================================

async def _send(message):
    bot = Bot(token=TELEGRAM_TOKEN)
    if len(message) <= 4000:
        await bot.send_message(chat_id=TELEGRAM_CHAT, text=message, parse_mode="Markdown")
    else:
        for i in range(0, len(message), 4000):
            await bot.send_message(chat_id=TELEGRAM_CHAT, text=message[i:i+4000], parse_mode="Markdown")
            await asyncio.sleep(0.5)

def send_telegram(message):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_send(message))
        loop.close()
    except Exception as e:
        logger.error(f"send_telegram error: {e}")


# ============================================================================
# SCHEDULED JOB HANDLERS
# ============================================================================

def _run_all_signals(vix, stock_cache=None):
    """Helper: runs all 4 signal types and returns them together."""
    if stock_cache is None:
        pnl_data    = get_portfolio_pnl()
        stock_cache = pnl_data.get("stock_cache", {})
    protection, growth = run_portfolio_signals(vix, stock_cache)
    wl_stocks          = run_watchlist_signals(vix)
    wl_options         = run_watchlist_options(vix)
    return protection, growth, wl_stocks, wl_options


def job_premarket_scan():
    """Pre-market scan: every 30min 6:30am–11:30am ET."""
    logger.info("⏰ Pre-market scan running...")
    try:
        vix      = get_vix()
        pnl_data = get_portfolio_pnl()
        sc       = pnl_data.get("stock_cache", {})
        protection, growth, wl_stocks, wl_options = _run_all_signals(vix, sc)
        if protection or growth or wl_stocks or wl_options:
            send_telegram(format_portfolio_signals(protection, growth, wl_stocks, wl_options, vix))
        else:
            logger.info("✅ Pre-market: no actionable signals this cycle")
        if datetime.now(EST).hour == 7:
            alerts = check_earnings_alerts()
            msg    = format_earnings_alerts(alerts)
            if msg:
                send_telegram(msg)
    except Exception as e:
        logger.error(f"job_premarket_scan: {e}")


def job_market_report():
    """Market hours: report + signals every 60min 9:30am–3:30pm ET."""
    logger.info("📊 Market report running...")
    try:
        vix      = get_vix()
        pnl_data = get_portfolio_pnl()
        market   = get_market_metrics()
        send_telegram(format_market_report(pnl_data, market, vix))
        sc = pnl_data.get("stock_cache", {})
        protection, growth, wl_stocks, wl_options = _run_all_signals(vix, sc)
        if protection or growth or wl_stocks or wl_options:
            time.sleep(2)
            send_telegram(format_portfolio_signals(protection, growth, wl_stocks, wl_options, vix))
    except Exception as e:
        logger.error(f"job_market_report: {e}")


def job_eod_summary():
    """EOD summary at 4:15pm ET."""
    logger.info("🔔 EOD summary running...")
    try:
        vix      = get_vix()
        pnl_data = get_portfolio_pnl()
        market   = get_market_metrics()
        send_telegram(format_eod_summary(pnl_data, market, vix))
    except Exception as e:
        logger.error(f"job_eod_summary: {e}")


def job_postmarket_scan():
    """Post-market scan: signal scan + reminders every 30min 4:00pm–6:00pm ET."""
    logger.info("📡 Post-market scan running...")
    try:
        vix = get_vix()
        reminder_msg = format_reminders_section()
        if reminder_msg:
            send_telegram(reminder_msg)
            time.sleep(2)
        protection, growth, wl_stocks, wl_options = _run_all_signals(vix)
        send_telegram(format_portfolio_signals(protection, growth, wl_stocks, wl_options, vix))
    except Exception as e:
        logger.error(f"job_postmarket_scan: {e}")


# ============================================================================
# TELEGRAM COMMAND HANDLERS
# ============================================================================

# ── Portfolio Management ──────────────────────────────────────────────────────

def _portfolio_summary_text():
    pnl_data = get_portfolio_pnl()
    positions = pnl_data.get("positions", {})
    if not positions:
        return "💼 Portfolio is empty. Use /add to add positions."

    stock_lines, opt_lines = [], []
    for key, pos in sorted(positions.items(), key=lambda x: -abs(x[1]["pnl"])):
        e = "🟢" if pos["pnl"] >= 0 else "🔴"
        if pos["asset_type"] == "stock":
            stock_lines.append(
                f"{e} *{pos['ticker']}*: {pos['shares']:.0f} @ ${pos['avg_cost']:.2f}\n"
                f"   ${pos['price']:.2f} | P&L: ${pos['pnl']:+.0f} ({pos['pnl_pct']:+.1f}%)"
            )
        else:
            strike_str = f"${pos['strike']:.0f}" if pos.get("strike") else ""
            etype = pos["asset_type"].upper()
            opt_lines.append(
                f"{e} *{pos['ticker']}* {strike_str} {etype} exp {pos.get('expiry','')} ({pos.get('contracts',1)}ct @ ${pos.get('avg_cost',0):.2f})\n"
                f"   P&L: ${pos['pnl']:+.0f} ({pos['pnl_pct']:+.1f}%) [{pos.get('status','')}]"
            )

    msg  = "💼 *PORTFOLIO*\n\n"
    if stock_lines:
        msg += "📈 *Stocks:*\n" + "\n".join(stock_lines) + "\n\n"
    if opt_lines:
        msg += "🎯 *Options:*\n" + "\n".join(opt_lines) + "\n\n"

    msg += "=" * 30 + "\n"
    e = "🟢" if pnl_data["total_pnl"] >= 0 else "🔴"
    daily_e = "🟢" if pnl_data["daily_pnl"] >= 0 else "🔴"
    msg += f"💰 Value: ${pnl_data['total_value']:,.0f} | Cost: ${pnl_data['total_cost']:,.0f}\n"
    msg += f"{e} *Total P&L: ${pnl_data['total_pnl']:+.0f} ({pnl_data['total_pnl_pct']:+.1f}%)*\n"
    msg += f"{daily_e} *Today: ${pnl_data['daily_pnl']:+.0f}*"
    return msg


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/portfolio — live P&L for all stocks and options"""
    await update.message.reply_text("⏳ Fetching live prices...")
    try:
        await update.message.reply_text(_portfolio_summary_text(), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add TICKER SHARES PRICE [stock|call|put] [STRIKE] [EXPIRY YYYY-MM-DD]

    Examples:
      /add NVDA 10 175.90              → stock (default)
      /add NVDA 1 12.00 call 200 2026-06-18  → call option
      /add SPY 2 5.50 put 500 2026-05-15     → put option
    """
    try:
        args = context.args
        if len(args) < 3:
            await update.message.reply_text(
                "❌ *Usage:*\n"
                "`/add TICKER SHARES PRICE` — stock\n"
                "`/add TICKER CONTRACTS PREMIUM call STRIKE EXPIRY` — call option\n"
                "`/add TICKER CONTRACTS PREMIUM put STRIKE EXPIRY` — put option\n\n"
                "Examples:\n"
                "`/add NVDA 10 175.90`\n"
                "`/add NVDA 1 12.00 call 200 2026-06-18`",
                parse_mode="Markdown"
            )
            return

        ticker     = args[0].upper()
        qty        = float(args[1])
        price      = float(args[2])
        asset_type = args[3].lower() if len(args) > 3 else "stock"
        strike     = float(args[4]) if len(args) > 4 else None
        expiry     = args[5] if len(args) > 5 else None

        if asset_type not in ("stock", "call", "put"):
            await update.message.reply_text("❌ Asset type must be: stock, call, or put")
            return

        key = _make_position_key(ticker, asset_type, strike, expiry)

        if key in live_portfolio and asset_type == "stock":
            # Weighted average for stocks
            old    = live_portfolio[key]
            new_sh = old["shares"] + qty
            new_avg = ((old["shares"] * old["avg_cost"]) + (qty * price)) / new_sh
            live_portfolio[key]["shares"]   = new_sh
            live_portfolio[key]["avg_cost"] = round(new_avg, 3)
            action = f"Updated: {new_sh:.0f} shares @ ${new_avg:.3f} avg"
        else:
            live_portfolio[key] = {
                "asset_type": asset_type,
                "ticker":     ticker,
                "shares":     qty if asset_type == "stock" else None,
                "avg_cost":   price,
                "entry_date": datetime.now(EST).strftime("%Y-%m-%d"),
                "strike":     strike,
                "expiry":     expiry,
                "contracts":  qty if asset_type != "stock" else None,
            }
            action = "New position added"

        save_portfolio(live_portfolio)

        type_label = asset_type.upper()
        if asset_type != "stock":
            msg = (f"✅ *ADDED: {qty:.0f}ct {ticker} {strike} {type_label} "
                   f"exp {expiry} @ ${price:.2f}/share*\n{action}\n"
                   f"Contract cost: ${price * 100 * qty:,.0f}")
        else:
            msg = (f"✅ *ADDED: {qty:.0f} {ticker} @ ${price:.2f}*\n{action}\n"
                   f"Position value: ${qty * price:,.0f}")
        await update.message.reply_text(msg, parse_mode="Markdown")

    except ValueError:
        await update.message.reply_text("❌ Invalid numbers. Check your input.")
    except Exception as e:
        logger.error(f"cmd_add: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /delete TICKER [stock|call|put] [STRIKE] [EXPIRY]
    /delete list — show all position keys
    """
    try:
        if not context.args:
            await update.message.reply_text("Usage: `/delete TICKER` or `/delete list`", parse_mode="Markdown")
            return

        if context.args[0].lower() == "list":
            lines = [f"`{k}` — {v['ticker']} {v['asset_type']}" for k, v in live_portfolio.items()]
            msg   = "📋 *Position keys:*\n" + "\n".join(lines) + "\n\nUse full key with `/delete KEY`"
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        ticker     = context.args[0].upper()
        asset_type = context.args[1].lower() if len(context.args) > 1 else "stock"
        strike     = float(context.args[2]) if len(context.args) > 2 else None
        expiry     = context.args[3] if len(context.args) > 3 else None

        key = _make_position_key(ticker, asset_type, strike, expiry)

        # Also try exact key match if user typed it directly
        if key not in live_portfolio and ticker in live_portfolio:
            key = ticker  # Legacy key

        if key not in live_portfolio:
            # Fuzzy match on ticker
            matches = [k for k, v in live_portfolio.items() if v["ticker"] == ticker]
            if not matches:
                await update.message.reply_text(f"❌ {ticker} not found. Use `/delete list` to see all keys.", parse_mode="Markdown")
                return
            if len(matches) == 1:
                key = matches[0]
            else:
                lines = [f"`{m}`" for m in matches]
                await update.message.reply_text(
                    f"Multiple positions for {ticker}:\n" + "\n".join(lines) + "\n\nSpecify: `/delete TICKER TYPE STRIKE EXPIRY`",
                    parse_mode="Markdown"
                )
                return

        pos = live_portfolio.pop(key)
        save_portfolio(live_portfolio)
        await update.message.reply_text(
            f"✅ Deleted: *{pos['ticker']}* {pos['asset_type'].upper()} position",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"cmd_delete: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_amend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /amend TICKER [stock] FIELD VALUE
    Fields: shares, avg_cost, contracts, strike, expiry
    Example: /amend RKLB shares 70
             /amend RKLB avg_cost 65.00
    """
    try:
        if len(context.args) < 3:
            await update.message.reply_text(
                "Usage: `/amend TICKER FIELD VALUE`\n"
                "Fields: shares, avg_cost, contracts, strike, expiry\n\n"
                "Examples:\n"
                "`/amend RKLB shares 70`\n"
                "`/amend RKLB avg_cost 65.00`",
                parse_mode="Markdown"
            )
            return

        ticker = context.args[0].upper()
        field  = context.args[1].lower()
        value  = context.args[2]

        # Find matching position
        matches = [k for k, v in live_portfolio.items() if v["ticker"] == ticker]
        if not matches:
            await update.message.reply_text(f"❌ {ticker} not found in portfolio.")
            return
        if len(matches) > 1:
            lines = [f"`{m}` — {live_portfolio[m]['asset_type']}" for m in matches]
            await update.message.reply_text(
                f"Multiple positions for {ticker}:\n" + "\n".join(lines) + "\n\nUse `/delete list` for exact keys.",
                parse_mode="Markdown"
            )
            return

        key = matches[0]
        valid_fields = {"shares", "avg_cost", "contracts", "strike", "expiry", "entry_date"}
        if field not in valid_fields:
            await update.message.reply_text(f"❌ Field '{field}' not valid. Choose from: {', '.join(valid_fields)}")
            return

        # Type-cast
        if field in ("shares", "contracts"):
            live_portfolio[key][field] = float(value)
        elif field in ("avg_cost", "strike"):
            live_portfolio[key][field] = float(value)
        else:
            live_portfolio[key][field] = value

        save_portfolio(live_portfolio)
        await update.message.reply_text(
            f"✅ Updated *{ticker}* — {field} → {value}",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid value. Numbers for shares/avg_cost/strike.")
    except Exception as e:
        logger.error(f"cmd_amend: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


# ── Watchlist Commands ────────────────────────────────────────────────────────

async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /watchlist add TICKER
    /watchlist remove TICKER
    /watchlist view
    """
    global live_watchlist
    try:
        if not context.args:
            # Default: show watchlist
            msg = "📋 *YOUR WATCHLIST*\n\n"
            msg += ", ".join(f"`{t}`" for t in live_watchlist) if live_watchlist else "_Empty_"
            msg += "\n\nCommands:\n`/watchlist add TICKER`\n`/watchlist remove TICKER`\n`/watchlist view`"
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        sub = context.args[0].lower()

        if sub == "view":
            msg = "📋 *YOUR WATCHLIST*\n\n"
            msg += ", ".join(f"`{t}`" for t in live_watchlist) if live_watchlist else "_Empty_"
            await update.message.reply_text(msg, parse_mode="Markdown")

        elif sub == "add":
            if len(context.args) < 2:
                await update.message.reply_text("Usage: `/watchlist add TICKER`", parse_mode="Markdown")
                return
            ticker = context.args[1].upper()
            if ticker in live_watchlist:
                await update.message.reply_text(f"ℹ️ {ticker} already on watchlist.")
                return
            live_watchlist.append(ticker)
            save_watchlist(live_watchlist)
            await update.message.reply_text(f"✅ *{ticker}* added to watchlist.", parse_mode="Markdown")

        elif sub == "remove":
            if len(context.args) < 2:
                await update.message.reply_text("Usage: `/watchlist remove TICKER`", parse_mode="Markdown")
                return
            ticker = context.args[1].upper()
            if ticker not in live_watchlist:
                await update.message.reply_text(f"❌ {ticker} not found on watchlist.")
                return
            live_watchlist.remove(ticker)
            save_watchlist(live_watchlist)
            await update.message.reply_text(f"✅ *{ticker}* removed from watchlist.", parse_mode="Markdown")

        else:
            await update.message.reply_text("Usage: `/watchlist add|remove|view TICKER`", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_watchlist: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


# ── Info & Scan Commands ──────────────────────────────────────────────────────

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/scan — full manual signal scan: portfolio + watchlist stocks + watchlist options"""
    await update.message.reply_text("🔍 Running full scan... ~90 seconds")
    try:
        vix      = get_vix()
        pnl_data = get_portfolio_pnl()
        sc       = pnl_data.get("stock_cache", {})
        protection, growth, wl_stocks, wl_options = _run_all_signals(vix, sc)
        await update.message.reply_text(
            format_portfolio_signals(protection, growth, wl_stocks, wl_options, vix),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"cmd_scan: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_vix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/vix — VIX level and sentiment"""
    vix = get_vix()
    if vix:
        msg = f"{vix['emoji']} *VIX: {vix['level']:.2f}*\n{vix['sentiment']}"
    else:
        msg = "❌ Could not fetch VIX."
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_capital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/capital AMOUNT — set capital budget for position sizing guidance"""
    global user_settings
    try:
        if not context.args:
            cap = user_settings.get("swing_capital", 2000)
            await update.message.reply_text(
                f"💰 Capital budget: *${cap:,}*\nChange: `/capital 3000`",
                parse_mode="Markdown"
            )
            return
        capital = float(context.args[0])
        if capital <= 0:
            await update.message.reply_text("❌ Must be a positive number.")
            return
        user_settings["swing_capital"] = capital
        save_settings(user_settings)
        await update.message.reply_text(
            f"✅ Capital budget updated to *${capital:,.0f}*",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Usage: `/capital 2000`", parse_mode="Markdown")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /analyze TICKER — full technical analysis with score, confidence, signals, and entry/exit suggestion.
    Separate from /remind which is purely for setting price/date alerts.
    """
    try:
        if not context.args:
            await update.message.reply_text(
                "📊 *Usage:* `/analyze TICKER`\n\nExamples:\n`/analyze RKLB`\n`/analyze NVDA`\n`/analyze SPY`\n\n"
                "_To set a price alert, use /remind instead._",
                parse_mode="Markdown"
            )
            return

        ticker = context.args[0].upper()
        await update.message.reply_text(f"🔍 Analyzing *{ticker}*... (~15 sec)", parse_mode="Markdown")

        data = get_stock_data(ticker)
        if not data:
            await update.message.reply_text(f"❌ No data for {ticker}. Check the ticker symbol.")
            return
        scored = score_ticker(data)
        if not scored:
            await update.message.reply_text(f"❌ Could not score {ticker}.")
            return

        score  = scored["score"]
        price  = data["current_price"]
        conf   = score_to_confidence(score)
        sig    = classify_signal(score)
        rsi    = scored.get("rsi")
        bb     = scored.get("bb")
        ema20  = scored.get("ema20")
        ema50  = scored.get("ema50")

        sig_labels = {
            "STRONG_BUY":  "🟢🟢 STRONG BUY",
            "MEDIUM_BUY":  "🟢 BUY",
            "STRONG_SELL": "🔴🔴 STRONG SELL",
            "WEAK_SELL":   "🔴 TRIM / REDUCE",
        }
        verdict = sig_labels.get(sig, "⏸️ HOLD / NEUTRAL")
        action  = "BUY" if sig in ("STRONG_BUY", "MEDIUM_BUY") else ("SELL" if sig in ("STRONG_SELL", "WEAK_SELL") else "WATCH")

        # Entry/exit suggestion
        if action == "BUY" and bb:
            suggestion = f"Entry near ${bb['lower']:.2f} (BB lower) | Stop: ${bb['lower'] * (1 - STOP_LOSS_PCT):.2f}"
        elif action == "SELL" and bb:
            suggestion = f"Trim near ${bb['upper']:.2f} (BB upper)"
        else:
            suggestion = "No clear entry — wait for stronger signal"

        # Earnings warning
        earn_warn = ""
        earn_date = EARNINGS_CALENDAR.get(ticker)
        if earn_date:
            days = (datetime.strptime(earn_date, "%Y-%m-%d").date() - datetime.now(EST).date()).days
            if 0 <= days <= 14:
                earn_warn = f"\n⚠️ *Earnings in {days} days* ({earn_date}) — size cautiously"

        # News catalyst
        catalyst = get_news_catalyst(ticker)

        # Confidence bar
        conf_bar = "▓" * int(conf * 10) + "░" * (10 - int(conf * 10))

        # In portfolio?
        in_portfolio = any(v["ticker"] == ticker for v in live_portfolio.values())
        in_watchlist = ticker in live_watchlist
        context_tag  = "📋 In portfolio" if in_portfolio else ("👁 On watchlist" if in_watchlist else "")

        msg  = f"📊 *{ticker} ANALYSIS*"
        if context_tag:
            msg += f" — {context_tag}"
        msg += f"\n\n"
        msg += f"Price: *${price:.2f}* | Daily: {data['daily_change_pct']:+.2f}%\n"
        msg += f"Score: *{score:+d}/6* | Confidence: *{conf:.0%}* [{conf_bar}]\n"
        msg += f"Verdict: {verdict}\n\n"

        msg += f"📐 *Key levels:*\n"
        if ema20 and ema50:
            trend = "↑ Uptrend" if ema20 > ema50 else "↓ Downtrend"
            msg += f"EMA20: ${ema20:.2f} | EMA50: ${ema50:.2f} — {trend}\n"
        if bb:
            msg += f"BB: ${bb['lower']:.2f} — ${bb['upper']:.2f} (mid ${bb['middle']:.2f})\n"
        if rsi is not None:
            rsi_note = "oversold" if rsi < 30 else ("overbought" if rsi > 70 else "neutral")
            msg += f"RSI: {rsi:.1f} ({rsi_note})\n"

        msg += f"\n💡 *Suggestion:* {suggestion}{earn_warn}\n\n"

        msg += f"*All signals:*\n"
        for d in scored["details"]:
            msg += f"  {d}\n"

        if catalyst:
            msg += f"\n📰 *Recent news:* {catalyst['impact']}\n"
            for h in catalyst["headlines"][:2]:
                msg += f"  • {h[:75]}{'…' if len(h) > 75 else ''}\n"

        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"Set a price alert: `/remind {ticker} {action} {price:.2f}`\n"
        msg += f"Add to watchlist: `/watchlist add {ticker}`"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"cmd_analyze: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remind TICKER BUY/SELL PRICE [YYYY-MM-DD] — set a price or date-based alert.
    For stock analysis, use /analyze instead.
    """
    try:
        if not context.args or len(context.args) < 3:
            await update.message.reply_text(
                "📅 *Set a price or date alert:*\n\n"
                "Price alert:\n`/remind NVDA BUY 190.00`\n\n"
                "Price + date alert:\n`/remind NVDA SELL 220.00 2026-05-15`\n\n"
                "View alerts: `/reminders`\n"
                "Delete: `/delreminder 1`\n\n"
                "_For stock analysis, use /analyze TICKER_",
                parse_mode="Markdown"
            )
            return

        ticker       = context.args[0].upper()
        action       = context.args[1].upper()
        target_price = float(context.args[2])
        remind_date  = context.args[3] if len(context.args) >= 4 else None

        if action not in ("BUY", "SELL", "WATCH"):
            await update.message.reply_text("❌ Action must be BUY, SELL, or WATCH.")
            return

        reminder = {
            "ticker":       ticker,
            "action":       action,
            "target_price": target_price,
            "remind_date":  remind_date,
            "note":         f"Set {datetime.now(EST).strftime('%Y-%m-%d')}",
            "added":        datetime.now(EST).strftime("%Y-%m-%d"),
        }
        reminders.append(reminder)
        save_reminders(reminders)

        data    = get_stock_data(ticker)
        current = data["current_price"] if data else None
        emoji   = "🟢" if action == "BUY" else "🔴"

        msg  = f"✅ *Reminder Set!*\n\n"
        msg += f"{emoji} {action} *{ticker}* @ ${target_price:.2f}\n"
        if current:
            diff = ((current - target_price) / target_price * 100)
            msg += f"Current: ${current:.2f} ({diff:+.1f}% away)\n"
        if remind_date:
            msg += f"Date: {remind_date}\n"
        msg += f"\nI'll alert you in the next scan cycle when the price is hit. 🔔\n"
        msg += f"_Use /reminders to view all | /analyze {ticker} for full analysis_"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except ValueError:
        await update.message.reply_text(
            "❌ Invalid price. Example: `/remind NVDA BUY 190.00`", parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"cmd_remind: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not reminders:
        await update.message.reply_text("📅 No reminders. Use `/remind TICKER` to add.", parse_mode="Markdown")
        return
    msg = "🔔 *YOUR REMINDERS*\n\n"
    for i, r in enumerate(reminders, 1):
        action = r["action"]
        emoji  = "🟢" if action == "BUY" else "🔴"
        msg += f"{i}. {emoji} {action} *{r['ticker']}*"
        if r.get("target_price"):
            msg += f" @ ${float(r['target_price']):.2f}"
        if r.get("remind_date"):
            msg += f" | 📅 {r['remind_date']}"
        if r.get("note"):
            msg += f"\n   _{r['note']}_"
        msg += "\n\n"
    msg += "Delete: `/delreminder 1`"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_delreminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("Usage: `/delreminder 1`", parse_mode="Markdown")
            return
        idx = int(context.args[0]) - 1
        if idx < 0 or idx >= len(reminders):
            await update.message.reply_text("❌ Invalid number. Use /reminders to see list.")
            return
        removed = reminders.pop(idx)
        save_reminders(reminders)
        await update.message.reply_text(f"✅ Deleted: {removed['action']} {removed['ticker']}")
    except ValueError:
        await update.message.reply_text("❌ Use a number. `/delreminder 1`", parse_mode="Markdown")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Milnai Trading Bot v3.0*\n\n"
        "*Portfolio Management:*\n"
        "/add TICKER QTY PRICE [call|put STRIKE EXPIRY]\n"
        "/delete TICKER [type STRIKE EXPIRY]\n"
        "/amend TICKER FIELD VALUE\n"
        "/portfolio — live P&L (stocks + options)\n\n"
        "*Watchlist:*\n"
        "/watchlist add|remove|view TICKER\n\n"
        "*Analysis:*\n"
        "/analyze TICKER — full technical analysis\n"
        "/scan — full portfolio + watchlist scan now\n"
        "/vix — fear index\n\n"
        "*Price Alerts:*\n"
        "/remind TICKER BUY|SELL PRICE [DATE]\n"
        "/reminders — view all alerts\n"
        "/delreminder N — delete alert #N\n\n"
        "*Settings:*\n"
        "/capital AMOUNT — set capital budget\n"
        "/help — this message\n\n"
        "📅 Auto alerts fire Mon–Fri during US market hours."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💬 Use commands. Type /help to see all.")


# ============================================================================
# MAIN SCHEDULER
# ============================================================================

def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.error("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — exiting")
        return

    scheduler = BackgroundScheduler(timezone=EST)

    # Pre-market: every 30min 6:30am–11:30am ET
    scheduler.add_job(job_premarket_scan, CronTrigger(
        hour="6-11", minute="0,30", day_of_week="mon-fri"),
        id="premarket_scan", name="Pre-Market Scan (30min)")

    # Market hours: every 60min 9:30am–3:30pm ET
    scheduler.add_job(job_market_report, CronTrigger(
        hour="9-15", minute=30, day_of_week="mon-fri"),
        id="market_report", name="Market Report (hourly)")

    # EOD summary at 4:15pm ET
    scheduler.add_job(job_eod_summary, CronTrigger(
        hour=16, minute=15, day_of_week="mon-fri"),
        id="eod_summary", name="EOD Summary")

    # Post-market scan: every 30min 4:00pm–6:00pm ET
    scheduler.add_job(job_postmarket_scan, CronTrigger(
        hour="16-18", minute="0,30", day_of_week="mon-fri"),
        id="postmarket_scan", name="Post-Market Scan (30min)")

    scheduler.start()

    stock_count = len(get_stock_positions())
    opt_count   = len(get_option_positions())

    logger.info("=" * 55)
    logger.info("✅ Trading Bot v3.1 — Portfolio + Watchlist Options")
    logger.info(f"   Portfolio: {stock_count} stocks, {opt_count} options")
    logger.info(f"   Watchlist: {len(live_watchlist)} tickers (options scan source)")
    logger.info(f"   Signal confidence gate: {MIN_CONFIDENCE:.0%}")
    logger.info(f"   Alert cooldown: {ALERT_COOLDOWN_MINUTES}min")
    logger.info(f"   Watchlist cap: {WATCHLIST_MAX_PER_CYCLE}/cycle")
    logger.info(f"   Finnhub news: {'✅ active' if FINNHUB_KEY else '⚠️ key missing'}")
    logger.info("=" * 55)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Portfolio management
    app.add_handler(CommandHandler("add",          cmd_add))
    app.add_handler(CommandHandler("delete",       cmd_delete))
    app.add_handler(CommandHandler("amend",        cmd_amend))
    app.add_handler(CommandHandler("portfolio",    cmd_portfolio))

    # Watchlist
    app.add_handler(CommandHandler("watchlist",    cmd_watchlist))

    # Analysis
    app.add_handler(CommandHandler("analyze",      cmd_analyze))

    # Reminders
    app.add_handler(CommandHandler("remind",       cmd_remind))
    app.add_handler(CommandHandler("reminders",    cmd_reminders))
    app.add_handler(CommandHandler("delreminder",  cmd_delreminder))

    # Scans & info
    app.add_handler(CommandHandler("scan",         cmd_scan))
    app.add_handler(CommandHandler("vix",          cmd_vix))
    app.add_handler(CommandHandler("capital",      cmd_capital))

    # Onboarding
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_help))

    # Free text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 Bot listening for commands...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
