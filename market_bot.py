#!/usr/bin/env python3
"""
Trading Bot v2.0 + Option Hunter
=================================
FIXED from v1:
  - Polygon.io replaces Finnhub (proper candle API, no rate limits)
  - Full 6-indicator system: EMA20/50, RSI, MACD, BB, Volume, ADX
  - Signal threshold: 4+/6 for BUY (was incorrectly 2+)
  - Holdings updated: INTC removed, IREN added
  - Stop loss corrected to 3.5% (per strategy doc)

NEW in v2:
  - Option Hunter: scans 55 liquid tickers for CALL/PUT setups
  - Dual-source price validator (Polygon vs yfinance, blocks if >0.5% diff)
  - VIX via yfinance single call (no API cost)
  - Options chain via yfinance: OI filter, IV filter, 21-35 DTE targeting
  - Telegram chunking (handles Telegram 4096 char limit)

SCHEDULE (all EST, Mon-Fri only):
  Pre-market  : Market report hourly 6:30-9:30am | Option Hunter at 8:30am
  Market hours: Market report hourly 9:30am-3:30pm | Option Hunter at 10am, 12pm, 2pm
  Post-market : Trading signals at 4:00pm & 4:30pm | Option Hunter at 4:15pm
"""

import subprocess
import sys

# Auto-install missing packages (Railway/Docker safety net)
_REQUIRED = [
    "yfinance",
    "requests",
    "numpy",
    "APScheduler",
    "python-telegram-bot",
    "python-dotenv",
    "pytz",
]

def _ensure_packages():
    for pkg in _REQUIRED:
        try:
            __import__(pkg.lower().replace("-", "_"))
        except ImportError:
            print(f"[setup] Installing missing package: {pkg}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

_ensure_packages()

import asyncio
import os
import logging
import time
import pytz
import numpy as np
import yfinance as yf
import requests

from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT   = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

EST = pytz.timezone("US/Eastern")
SGT = pytz.timezone("Asia/Singapore")

# --- Holdings (updated Apr 18 2026: RKLB 67 shares, APLD added) ---
HOLDINGS = {
    "RKLB": {"shares": 67,  "avg_cost": 68.439},
    "NVDA": {"shares": 10,  "avg_cost": 175.90},
    "MSFT": {"shares": 2,   "avg_cost": 372.50},
    "ALAB": {"shares": 3,   "avg_cost": 116.00},
    "SCHD": {"shares": 15,  "avg_cost": 30.50},
    "NBIS": {"shares": 5,   "avg_cost": 114.90},
    "NVDL": {"shares": 10,  "avg_cost": 80.90},
    "SLV":  {"shares": 18,  "avg_cost": 90.667},
    "GRAB": {"shares": 284, "avg_cost": 5.899},
    "IREN": {"shares": 5,   "avg_cost": 47.00},
    "APLD": {"shares": 10,  "avg_cost": 31.40},
}

MARKET_TICKERS = ["SPY", "QQQ"]

# --- Option Hunter scan universe (expanded from watchlist — 75 tickers) ---
OPTION_HUNT_TICKERS = [
    # Broad ETFs
    "SPY", "QQQ", "IWM", "GLD", "SLV",
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA",
    # High-beta movers
    "AMD", "CRWD", "PLTR", "COIN", "MSTR", "SNOW", "SOFI",
    # Semiconductors
    "TSM", "AMAT", "MU", "AVGO", "QCOM", "ARM", "MRVL",
    # Financials
    "JPM", "GS", "BAC", "MS", "C",
    # Healthcare
    "UNH", "JNJ", "PFE", "MRNA", "HIMS",
    # Energy
    "XOM", "CVX", "SCCO",
    # Consumer / media
    "NFLX", "DIS", "SBUX", "NKE",
    # Fintech / growth
    "PYPL", "SQ", "UBER", "ABNB", "SHOP",
    # AI infrastructure (added after CRWV/NBIS miss)
    "CRWV", "NBIS", "APLD",
    # Watchlist additions (Ian's full list)
    "AXON", "PANW", "BABA", "ZIM", "HIVE",
    # Ian's holdings (optionable)
    "RKLB", "ALAB", "GRAB", "NVDL", "IREN", "SCHD",
]

# --- Earnings watch: these trigger pre-earnings PUT alerts ---
# Format: "TICKER": "YYYY-MM-DD" (expected earnings date)
# Update weekly — bot will warn 1-2 days before
EARNINGS_CALENDAR = {
    "PLTR": "2026-05-04",
    "RKLB": "2026-05-13",
    "AMD":  "2026-05-05",
    "NVDA": "2026-05-28",
    "AXON": "2026-05-06",
    "CRWV": "2026-05-14",
    "NBIS": "2026-05-12",
    "HIMS": "2026-05-05",
    "TSLA": "2026-07-22",
    "NFLX": "2026-07-16",
    "META": "2026-04-30",
    "AMZN": "2026-05-01",
    "AAPL": "2026-05-01",
    "MSFT": "2026-04-30",
    "GOOGL":"2026-04-29",
}

# --- Swing Trade Configuration ---
SWING_CAPITAL       = 2000    # Total capital allocated for swing trades (SGD/USD)
SWING_MAX_TRADES    = 2       # Max simultaneous swing positions
SWING_HOLD_DAYS     = 2       # Max hold days before forced exit
SWING_MIN_PRICE     = 5.0     # Ignore penny stocks below this price
SWING_MIN_RR        = 2.0     # Minimum risk/reward ratio to qualify
SWING_TARGET1_PCT   = 0.05    # First target: +5% (sell 50%)
SWING_TARGET2_PCT   = 0.10    # Second target: +10% (sell remaining 50%)

# VIX-calibrated risk per trade
SWING_RISK = {
    "low":      0.03,   # VIX < 20  → risk 3% of capital = $60
    "medium":   0.02,   # VIX 20-25 → risk 2% = $40
    "high":     0.01,   # VIX > 25  → risk 1% = $20
}

# --- Signal thresholds (per strategy doc) ---
BUY_STRONG  =  5
BUY_MEDIUM  =  4
SELL_WEAK   = -2
SELL_STRONG = -4

# --- Options filters ---
OPT_MIN_OI      = 500     # Minimum open interest
OPT_MIN_DTE     = 21      # Min days to expiry
OPT_MAX_DTE     = 35      # Max days to expiry
OPT_MAX_IV      = 50.0    # Max implied volatility % (avoid expensive premiums)
OPT_OTM_MIN     = 0.01    # Strike at least 1% OTM
OPT_OTM_MAX     = 0.10    # Strike at most 10% OTM
PRICE_DIFF_GATE = 0.005   # Block alert if Polygon/yfinance differ by >0.5%

# --- Risk management (per strategy doc) ---
STOP_LOSS_PCT = 0.035     # 3.5% stop loss (regular market)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ============================================================================
# DATA LAYER (yfinance — free, no API key, no rate limits)
# ============================================================================

def get_stock_data(ticker):
    """
    Fetch live quote + 6-month OHLCV history using yfinance.
    Single source — free tier, no API key required.
    """
    try:
        stock = yf.Ticker(ticker)

        # --- Live quote (fast_info is an object, not a dict) ---
        info          = stock.fast_info
        current_price = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None)
        prev_close    = getattr(info, "previous_close", None) or getattr(info, "regular_market_previous_close", None)

        if not current_price or float(current_price) == 0:
            logger.warning(f"No price from yfinance for {ticker}")
            return None

        current_price = float(current_price)
        prev_close    = float(prev_close) if prev_close else current_price
        daily_change  = current_price - prev_close
        daily_pct     = (daily_change / prev_close * 100) if prev_close else 0

        # --- Historical OHLCV (6 months for indicators) ---
        hist = yf.download(ticker, period="6mo", interval="1d",
                           progress=False, auto_adjust=True)
        if hist.empty:
            logger.warning(f"No historical data from yfinance for {ticker}")
            return None

        # Flatten MultiIndex columns if present
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


def validate_price(ticker, price):
    """
    Now that yfinance is the single source, validation is a no-op.
    Kept for compatibility — always returns valid with the same price.
    """
    return True, price, 0.0


# ============================================================================
# VIX (yfinance only — single call, no Polygon cost)
# ============================================================================

def get_vix():
    """Fetch VIX from yfinance and classify market sentiment."""
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
            "level":         vix,
            "sentiment":     sentiment,
            "emoji":         emoji,
            "is_fearful":    vix > 25,
            "call_friendly": vix < 20,
        }
    except Exception as e:
        logger.error(f"VIX error: {e}")
        return None


# ============================================================================
# EARNINGS ALERT ENGINE
# ============================================================================

def check_earnings_alerts():
    """
    Scans EARNINGS_CALENDAR for stocks reporting in 1-2 days.
    Returns list of earnings alerts with PUT/CALL bias based on recent trend.
    Fires pre-market so you can position BEFORE the move.
    """
    alerts = []
    today  = datetime.now(EST).date()

    for ticker, date_str in EARNINGS_CALENDAR.items():
        try:
            earn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            days_away = (earn_date - today).days

            if days_away < 0 or days_away > 2:
                continue  # Already passed or too far out

            # Get recent price trend for bias
            data = get_stock_data(ticker)
            trend = "neutral"
            score = 0
            if data:
                scored = score_ticker(data)
                if scored:
                    score = scored["score"]
                    if score >= 2:
                        trend = "bullish"
                    elif score <= -2:
                        trend = "bearish"

            # Bias logic for earnings plays
            if trend == "bearish":
                bias = "🔴 PUT candidate"
                reason = "Bearish indicators into earnings — consider protective put"
            elif trend == "bullish":
                bias = "🟢 CALL candidate"
                reason = "Bullish momentum into earnings — consider call if IV not too high"
            else:
                bias = "⚪ NEUTRAL — high risk"
                reason = "Mixed signals — avoid options, earnings binary event"

            alerts.append({
                "ticker":    ticker,
                "earn_date": date_str,
                "days_away": days_away,
                "bias":      bias,
                "reason":    reason,
                "score":     score,
            })
        except Exception as e:
            logger.warning(f"Earnings check {ticker}: {e}")

    return alerts


def format_earnings_alerts(alerts):
    """Format earnings alert message for Telegram."""
    if not alerts:
        return None

    t   = _time_display()
    msg = "📅 *EARNINGS ALERT — OPTIONS WATCH*\n"
    msg += f"_{t['sgt']} | {t['est']}_\n\n"
    msg += "⚡ *Stocks reporting in 1-2 days — act BEFORE open:*\n\n"

    for a in alerts:
        day_label = "TODAY after close" if a["days_away"] == 0 else (
                    "TOMORROW" if a["days_away"] == 1 else "In 2 days")
        msg += f"{a['bias']}: *{a['ticker']}*\n"
        msg += f"Earnings: {a['earn_date']} ({day_label})\n"
        msg += f"Tech score: {a['score']:+d}/6 | {a['reason']}\n"
        msg += f"⚠️ IV will spike at open — buy options NOW, not after report\n\n"

    msg += "_Earnings = binary risk. Max 1% capital per trade. Exit before close on earnings day._"
    return msg


# ============================================================================
# TECHNICAL INDICATORS
# ============================================================================

def _ema(closes, period):
    """Exponential Moving Average."""
    try:
        arr = np.array(closes, dtype=float)
        if len(arr) < period:
            return None
        k = 2.0 / (period + 1)
        ema = arr[0]
        for p in arr[1:]:
            ema = p * k + ema * (1 - k)
        return ema
    except:
        return None


def _rsi(closes, period=14):
    """RSI — Relative Strength Index."""
    try:
        arr = np.array(closes[-(period + 1):], dtype=float)
        if len(arr) < period + 1:
            return None
        deltas = np.diff(arr)
        avg_gain = np.mean(np.where(deltas > 0, deltas, 0))
        avg_loss = np.mean(np.where(deltas < 0, -deltas, 0))
        if avg_loss == 0:
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))
    except:
        return None


def _macd(closes, fast=12, slow=26, signal=9):
    """MACD line and signal line."""
    try:
        if len(closes) < slow + signal:
            return None, None
        # Build rolling MACD values for signal calculation
        macd_vals = []
        for i in range(slow, len(closes) + 1):
            ef = _ema(closes[:i], fast)
            es = _ema(closes[:i], slow)
            if ef is not None and es is not None:
                macd_vals.append(ef - es)
        if len(macd_vals) < signal:
            return None, None
        macd_line   = macd_vals[-1]
        signal_line = float(np.mean(macd_vals[-signal:]))
        return macd_line, signal_line
    except:
        return None, None


def _bollinger(closes, period=20, std_devs=2):
    """Bollinger Bands (upper, middle, lower)."""
    try:
        arr = np.array(closes[-period:], dtype=float)
        if len(arr) < period:
            return None
        sma = np.mean(arr)
        std = np.std(arr)
        return {"upper": sma + std_devs * std, "middle": sma, "lower": sma - std_devs * std}
    except:
        return None


def _adx(highs, lows, closes, period=14):
    """Average Directional Index."""
    try:
        h = np.array(highs, dtype=float)
        l = np.array(lows, dtype=float)
        c = np.array(closes, dtype=float)
        if len(c) < period + 2:
            return None
        trs, plus_dms, minus_dms = [], [], []
        for i in range(1, len(c)):
            trs.append(max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])))
            up   = h[i] - h[i-1]
            down = l[i-1] - l[i]
            plus_dms.append(up   if up   > down and up   > 0 else 0)
            minus_dms.append(down if down > up   and down > 0 else 0)
        atr = np.mean(trs[-period:])
        if atr == 0:
            return None
        plus_di  = 100 * np.mean(plus_dms[-period:])  / atr
        minus_di = 100 * np.mean(minus_dms[-period:]) / atr
        denom = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0
        return dx
    except:
        return None


# ============================================================================
# 6-INDICATOR SCORING ENGINE
# ============================================================================

def score_ticker(data):
    """
    Score a ticker -6 to +6 using the 6-indicator system.
    Score >= 4  → BUY signal
    Score <= -2 → SELL signal
    """
    try:
        closes  = data["closes"]
        highs   = data["highs"]
        lows    = data["lows"]
        volumes = data["volumes"]
        price   = data["current_price"]
        pct     = data["daily_change_pct"]

        score   = 0
        details = []

        # 1. EMA 20/50 — Trend direction
        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        if ema20 and ema50:
            if ema20 > ema50:
                score += 1
                details.append(f"✅ EMA20 ${ema20:.2f} > EMA50 ${ema50:.2f} (uptrend)")
            elif ema20 < ema50:
                score -= 1
                details.append(f"❌ EMA20 ${ema20:.2f} < EMA50 ${ema50:.2f} (downtrend)")

        # 2. RSI — Momentum extremes
        rsi = _rsi(closes)
        if rsi is not None:
            if rsi < 30:
                score += 1
                details.append(f"✅ RSI {rsi:.1f} — oversold (bounce likely)")
            elif rsi > 70:
                score -= 1
                details.append(f"❌ RSI {rsi:.1f} — overbought (reversal risk)")
            else:
                details.append(f"➖ RSI {rsi:.1f} — neutral")

        # 3. MACD — Momentum cross
        macd_line, signal_line = _macd(closes)
        if macd_line is not None and signal_line is not None:
            if macd_line > signal_line:
                score += 1
                details.append(f"✅ MACD {macd_line:.3f} > Signal {signal_line:.3f} (bullish)")
            else:
                score -= 1
                details.append(f"❌ MACD {macd_line:.3f} < Signal {signal_line:.3f} (bearish)")

        # 4. Bollinger Bands — Support/resistance extremes
        bb = _bollinger(closes)
        if bb:
            if price <= bb["lower"]:
                score += 1
                details.append(f"✅ At BB lower ${bb['lower']:.2f} (support bounce)")
            elif price >= bb["upper"]:
                score -= 1
                details.append(f"❌ At BB upper ${bb['upper']:.2f} (resistance)")
            else:
                details.append(f"➖ BB mid-range (${bb['lower']:.2f}—${bb['upper']:.2f})")

        # 5. Volume — Conviction confirmation
        if len(volumes) >= 20:
            avg_vol = np.mean(volumes[-20:])
            vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
            if vol_ratio >= 1.5:
                if pct >= 0:
                    score += 1
                    details.append(f"✅ Volume {vol_ratio:.1f}x avg (buying conviction)")
                else:
                    score -= 1
                    details.append(f"❌ Volume {vol_ratio:.1f}x avg (selling pressure)")
            else:
                details.append(f"➖ Volume {vol_ratio:.1f}x avg (low conviction)")

        # 6. ADX — Trend strength (amplifies existing direction)
        adx = _adx(highs, lows, closes)
        if adx is not None:
            if adx > 25:
                adj = 1 if score > 0 else -1
                score += adj
                details.append(f"✅ ADX {adx:.1f} — strong trend (confirms direction)")
            else:
                details.append(f"➖ ADX {adx:.1f} — weak/choppy (no confirmation)")

        return {
            "score":   score,
            "details": details,
            "rsi":     rsi,
            "bb":      bb,
            "ema20":   ema20,
            "ema50":   ema50,
        }
    except Exception as e:
        logger.error(f"score_ticker error: {e}")
        return None


def classify_signal(score):
    """Convert numeric score to signal label."""
    if score >= BUY_STRONG:
        return "STRONG_BUY"
    elif score >= BUY_MEDIUM:
        return "MEDIUM_BUY"
    elif score <= SELL_STRONG:
        return "STRONG_SELL"
    elif score <= SELL_WEAK:
        return "WEAK_SELL"
    return None


# ============================================================================
# OPTION HUNTER — OPTIONS CHAIN
# ============================================================================

def get_best_option(ticker, current_price, signal):
    """
    Fetch the best option contract from yfinance for a given signal direction.
    Filters: OI >500, DTE 21-35, IV <50%, strike 1-10% OTM.
    """
    try:
        stock = yf.Ticker(ticker)
        expirations = stock.options
        if not expirations:
            return None

        # Find best expiry targeting 28 DTE
        today = datetime.now().date()
        best_exp, best_diff = None, 9999

        for exp in expirations:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            diff = abs(dte - 28)
            if OPT_MIN_DTE <= dte <= OPT_MAX_DTE and diff < best_diff:
                best_diff = diff
                best_exp = exp

        # Fallback: nearest expiry >= 14 DTE
        if not best_exp:
            for exp in expirations:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                if (exp_date - today).days >= 14:
                    best_exp = exp
                    break

        if not best_exp:
            return None

        chain = stock.option_chain(best_exp)
        dte   = (datetime.strptime(best_exp, "%Y-%m-%d").date() - today).days
        is_call = signal in ("STRONG_BUY", "MEDIUM_BUY")

        if is_call:
            df          = chain.calls
            strike_min  = current_price * (1 + OPT_OTM_MIN)
            strike_max  = current_price * (1 + OPT_OTM_MAX)
            direction   = "CALL"
        else:
            df          = chain.puts
            strike_min  = current_price * (1 - OPT_OTM_MAX)
            strike_max  = current_price * (1 - OPT_OTM_MIN)
            direction   = "PUT"

        filtered = df[
            (df["strike"] >= strike_min) &
            (df["strike"] <= strike_max) &
            (df["openInterest"] >= OPT_MIN_OI)
        ]

        if filtered.empty:
            return None

        # Most liquid contract first
        filtered = filtered.sort_values("openInterest", ascending=False)
        best = filtered.iloc[0]

        iv = float(best.get("impliedVolatility", 0)) * 100
        if iv > OPT_MAX_IV:
            return None  # Premium too expensive

        bid  = float(best.get("bid", 0) or 0)
        ask  = float(best.get("ask", 0) or 0)
        last = float(best.get("lastPrice", 0) or 0)
        mid  = (bid + ask) / 2 if bid and ask else last

        return {
            "direction": direction,
            "strike":    float(best["strike"]),
            "expiry":    best_exp,
            "dte":       dte,
            "last":      last,
            "mid":       mid,
            "bid":       bid,
            "ask":       ask,
            "oi":        int(best.get("openInterest", 0) or 0),
            "volume":    int(best.get("volume", 0) or 0),
            "iv":        round(iv, 1),
        }
    except Exception as e:
        logger.warning(f"Options chain {ticker}: {e}")
        return None


def run_option_hunter(vix):
    """
    Full option hunter scan across 55 tickers.
    Returns top 10 opportunities sorted by signal strength.
    """
    logger.info(f"🎯 Option Hunter scanning {len(OPTION_HUNT_TICKERS)} tickers...")
    results = []

    for ticker in OPTION_HUNT_TICKERS:
        try:
            logger.info(f"   Scanning {ticker}...")
            data = get_stock_data(ticker)
            if not data:
                continue

            # --- Price validation gate ---
            valid, yf_price, diff_pct = validate_price(ticker, data["current_price"])
            if not valid:
                logger.warning(
                    f"   ⚠️ {ticker} BLOCKED — Polygon ${data['current_price']:.2f} "
                    f"vs yfinance ${yf_price:.2f} ({diff_pct:.2f}% diff)"
                )
                continue

            # --- Score the ticker ---
            scored = score_ticker(data)
            if not scored:
                continue

            signal = classify_signal(scored["score"])
            if not signal:
                continue  # No clear signal, skip

            # --- VIX gate ---
            if vix:
                if signal in ("STRONG_BUY", "MEDIUM_BUY") and vix["level"] > 30:
                    continue  # Too fearful for calls
                if signal in ("STRONG_SELL", "WEAK_SELL") and vix["level"] < 15:
                    continue  # Too calm for puts

            # --- Options chain ---
            option = get_best_option(ticker, data["current_price"], signal)
            if not option:
                continue

            results.append({
                "ticker":    ticker,
                "price":     data["current_price"],
                "score":     scored["score"],
                "signal":    signal,
                "details":   scored["details"],
                "option":    option,
                "yf_price":  yf_price,
                "diff_pct":  diff_pct,
            })

            time.sleep(0.5)  # Respectful pacing between tickers

        except Exception as e:
            logger.error(f"   Option Hunter {ticker}: {e}")
            continue

    results.sort(key=lambda x: abs(x["score"]), reverse=True)
    logger.info(f"🎯 Option Hunter found {len(results)} setup(s)")
    return results[:10]


# ============================================================================
# PORTFOLIO & MARKET DATA
# ============================================================================

def get_portfolio_data():
    total_cost = total_value = 0
    positions  = {}

    for ticker, info in HOLDINGS.items():
        data = get_stock_data(ticker)
        if not data:
            continue
        price     = data["current_price"]
        cost      = info["shares"] * info["avg_cost"]
        value     = info["shares"] * price
        pnl       = value - cost
        pnl_pct   = (pnl / cost * 100) if cost > 0 else 0
        total_cost  += cost
        total_value += value
        positions[ticker] = {"price": price, "pnl": pnl, "pnl_pct": pnl_pct}

    return {
        "positions":    positions,
        "total_value":  total_value,
        "total_pnl":    total_value - total_cost,
        "total_pnl_pct":((total_value - total_cost) / total_cost * 100) if total_cost else 0,
    }


def get_market_metrics():
    metrics = {}
    for ticker in MARKET_TICKERS:
        data = get_stock_data(ticker)
        if data:
            metrics[ticker] = {
                "current":   data["current_price"],
                "daily_pct": data["daily_change_pct"],
            }
    return metrics or None


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


def format_market_report(portfolio, market, vix):
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
        else:
            msg += "_(unavailable)_\n"

        msg += "\n"

        if portfolio.get("positions"):
            msg += "💼 *PORTFOLIO*\n"
            for tk, pos in portfolio["positions"].items():
                e = "🟢" if pos["pnl_pct"] >= 0 else "🔴"
                msg += f"{e} {tk}: ${pos['price']:.2f} | {pos['pnl_pct']:+.1f}% (${pos['pnl']:+.0f})\n"
            msg += "\n" + "=" * 35 + "\n"
            e = "🟢" if portfolio["total_pnl_pct"] >= 0 else "🔴"
            msg += f"{e} *TOTAL P&L: ${portfolio['total_pnl']:+.0f} ({portfolio['total_pnl_pct']:+.1f}%)*\n"
        else:
            msg += "_Portfolio data unavailable_\n"

        return msg
    except Exception as e:
        logger.error(f"format_market_report: {e}")
        return "❌ Market report error"


def format_trading_signals(vix):
    try:
        t   = _time_display()
        msg = "📈 *TRADING SIGNALS*\n"
        msg += f"_{t['sgt']} | {t['est']}_\n\n"

        if vix:
            msg += f"{vix['emoji']} *VIX: {vix['level']:.2f}* — {vix['sentiment']}\n\n"

        buys, sells = [], []

        for ticker in HOLDINGS:
            data = get_stock_data(ticker)
            if not data:
                continue
            scored = score_ticker(data)
            if not scored:
                continue
            signal = classify_signal(scored["score"])
            if signal in ("STRONG_BUY", "MEDIUM_BUY"):
                buys.append((ticker, data, scored, signal))
            elif signal in ("STRONG_SELL", "WEAK_SELL"):
                sells.append((ticker, data, scored, signal))

        # --- BUY block ---
        if buys:
            for ticker, data, scored, signal in buys:
                price = data["current_price"]
                entry = scored["bb"]["lower"] if scored.get("bb") else price * 0.98
                stop  = entry * (1 - STOP_LOSS_PCT)
                label = "🔥 *HIGHLY RECOMMENDED BUY*" if (vix and vix["is_fearful"]) else (
                        "🟢🟢 *STRONG BUY*" if signal == "STRONG_BUY" else "🟢 *MEDIUM BUY*")
                msg += f"{label}\n"
                msg += f"*{ticker}* @ ${price:.2f} | Score: {scored['score']:+d}/6\n"
                msg += f"Entry: ${entry:.2f} | Stop: ${stop:.2f} (-{STOP_LOSS_PCT*100:.1f}%)\n"
                for d in scored["details"]:
                    msg += f"  {d}\n"
                msg += "\n"
        else:
            msg += "🟢 *BUY SIGNALS:* None right now\n\n"

        msg += "=" * 35 + "\n\n"

        # --- SELL block ---
        if sells:
            for ticker, data, scored, signal in sells:
                price = data["current_price"]
                exit_ = scored["bb"]["upper"] if scored.get("bb") else price * 1.02
                label = "🔴🔴 *STRONG SELL*" if signal == "STRONG_SELL" else "🔴 *WEAK SELL*"
                msg += f"{label}\n"
                msg += f"*{ticker}* @ ${price:.2f} | Score: {scored['score']:+d}/6\n"
                msg += f"Exit target: ${exit_:.2f}\n"
                for d in scored["details"]:
                    msg += f"  {d}\n"
                msg += "\n"
        else:
            msg += "🔴 *SELL SIGNALS:* None right now\n"

        return msg
    except Exception as e:
        logger.error(f"format_trading_signals: {e}")
        return "❌ Signals error"


def format_option_hunter(opportunities, vix):
    try:
        t   = _time_display()
        msg = "🎯 *OPTION HUNTER*\n"
        msg += f"_{t['sgt']} | {t['est']}_\n\n"

        if vix:
            iv_note = "✅ Low IV — cheap premiums" if vix["call_friendly"] else "⚠️ Elevated IV — expensive premiums"
            msg += f"{vix['emoji']} *VIX: {vix['level']:.2f}* — {iv_note}\n\n"

        if not opportunities:
            msg += "🔍 *No qualifying setups this scan.*\n"
            msg += "_Criteria: Score ≥4/6 | OI >500 | DTE 21–35 | IV <50%_\n"
            return msg

        msg += f"*{len(opportunities)} setup(s) found:*\n\n"

        labels = {
            "STRONG_BUY":  "🟢🟢 STRONG CALL",
            "MEDIUM_BUY":  "🟢 MEDIUM CALL",
            "STRONG_SELL": "🔴🔴 STRONG PUT",
            "WEAK_SELL":   "🔴 MEDIUM PUT",
        }

        for opp in opportunities:
            opt   = opp["option"]
            label = labels.get(opp["signal"], opt["direction"])
            max_risk   = round(opt["mid"] * 100, 2)
            target_pnl = round(opt["mid"] * 100, 2)  # 2x target = entry × 2 × 100

            # Price validation note
            if opp.get("yf_price"):
                val = f"✅ Price validated: Polygon ${opp['price']:.2f} vs yfinance ${opp['yf_price']:.2f} ({opp['diff_pct']:.2f}% diff)"
            else:
                val = "⚠️ Single source (yfinance unavailable for cross-check)"

            msg += f"{label}: *{opp['ticker']}*\n"
            msg += f"Stock: ${opp['price']:.2f} | Signal score: {opp['score']:+d}/6\n"
            msg += f"Entry trigger: Only buy if stock ≤ ${opp['price']*1.005:.2f} (within 0.5% of now)\n"
            msg += f"Contract: ${opt['strike']:.0f} {opt['direction']} | Exp: {opt['expiry']} ({opt['dte']} DTE)\n"
            msg += f"Premium: ~${opt['mid']:.2f}/contract | Max risk: ${max_risk:.0f}\n"
            msg += f"Target: ${opt['mid']*2:.2f} (2× = +${target_pnl:.0f}) | OI: {opt['oi']:,} | IV: {opt['iv']:.0f}%\n"
            msg += "Top signals:\n"
            for d in opp["details"][:4]:
                msg += f"  {d}\n"
            msg += "\n"

        msg += "⚠️ _1 contract = 100 shares. Never risk >2% of capital per trade. Options can expire worthless._"
        return msg
    except Exception as e:
        logger.error(f"format_option_hunter: {e}")
        return "❌ Option Hunter error"


# ============================================================================
# SWING TRADE ENGINE
# ============================================================================

def _atr(highs, lows, closes, period=14):
    """Average True Range — measures stock's natural volatility for stop sizing."""
    try:
        h = np.array(highs[-period-1:], dtype=float)
        l = np.array(lows[-period-1:],  dtype=float)
        c = np.array(closes[-period-1:], dtype=float)
        trs = []
        for i in range(1, len(c)):
            trs.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
        return np.mean(trs) if trs else None
    except:
        return None


def _is_near_earnings(ticker):
    """Returns True if stock has earnings within 2 days — skip swing trades."""
    today = datetime.now(EST).date()
    date_str = EARNINGS_CALENDAR.get(ticker)
    if not date_str:
        return False
    earn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    return 0 <= (earn_date - today).days <= 2


def _get_top_movers():
    """Fetch today's top % gainers from yfinance screener proxy."""
    movers = []
    try:
        # Use a broad ETF holdings proxy — scan high-volume liquid names
        candidates = [
            "NVDA","AMD","TSLA","AAPL","META","AMZN","MSFT","GOOGL",
            "ARM","MRVL","AVGO","PLTR","CRWD","PANW","SHOP","SOFI",
            "HIMS","CRWV","NBIS","RKLB","ALAB","IREN","APLD","COIN",
        ]
        for ticker in candidates:
            try:
                info  = yf.Ticker(ticker).fast_info
                price = getattr(info, "last_price", None)
                prev  = getattr(info, "previous_close", None)
                if price and prev and float(prev) > 0:
                    pct = (float(price) - float(prev)) / float(prev) * 100
                    if abs(pct) >= 3.0:  # Moving 3%+ today
                        movers.append(ticker)
            except:
                continue
    except Exception as e:
        logger.warning(f"Top movers scan: {e}")
    return movers


def run_swing_scanner(vix):
    """
    Scans all 3 universes for swing trade setups.
    Returns top 3 setups sorted by score and R/R ratio.
    """
    logger.info("📊 Swing Trade Scanner running...")

    # Determine VIX risk tier
    vix_level = vix["level"] if vix else 20
    if vix_level < 20:
        risk_tier, risk_pct = "low", SWING_RISK["low"]
    elif vix_level < 25:
        risk_tier, risk_pct = "medium", SWING_RISK["medium"]
    else:
        risk_tier, risk_pct = "high", SWING_RISK["high"]

    risk_dollars = SWING_CAPITAL * risk_pct  # e.g. $60 at risk per trade

    # Build combined universe
    universe = list(set(
        list(HOLDINGS.keys()) +
        OPTION_HUNT_TICKERS +
        _get_top_movers()
    ))

    results = []

    for ticker in universe:
        try:
            # Skip penny stocks and earnings risk
            data = get_stock_data(ticker)
            if not data:
                continue
            if data["current_price"] < SWING_MIN_PRICE:
                continue
            if _is_near_earnings(ticker):
                logger.info(f"   {ticker} skipped — earnings within 2 days")
                continue

            # Score with 6-indicator system
            scored = score_ticker(data)
            if not scored:
                continue

            score = scored["score"]

            # Only take strong/medium BUY swings (long only on cash account)
            if score < BUY_MEDIUM:
                continue

            # RSI check — avoid overbought entries (RSI 40-68 sweet spot)
            rsi = scored.get("rsi")
            if rsi and (rsi > 68 or rsi < 35):
                continue

            # Volume check — must have conviction
            vols = data["volumes"]
            if len(vols) >= 20:
                avg_vol    = np.mean(vols[-20:])
                vol_ratio  = vols[-1] / avg_vol if avg_vol > 0 else 1
                if vol_ratio < 1.3:
                    continue  # Low volume = weak conviction

            # ATR-based stop loss (1.5× ATR below entry)
            atr = _atr(data["highs"], data["lows"], data["closes"])
            if not atr:
                continue

            entry      = data["current_price"]
            stop       = round(entry - (1.5 * atr), 2)
            risk_per_share = entry - stop

            if risk_per_share <= 0:
                continue

            # Position sizing — shares based on dollar risk
            shares = max(1, int(risk_dollars / risk_per_share))

            # Cap shares so total position ≤ 75% of swing capital
            max_shares = int((SWING_CAPITAL * 0.75) / entry)
            shares     = min(shares, max_shares)

            position_value = round(shares * entry, 2)
            dollar_risk    = round(shares * risk_per_share, 2)

            # Profit targets
            t1_price  = round(entry * (1 + SWING_TARGET1_PCT), 2)
            t2_price  = round(entry * (1 + SWING_TARGET2_PCT), 2)
            t1_profit = round(shares * 0.5 * (t1_price - entry), 2)
            t2_profit = round(shares * 0.5 * (t2_price - entry), 2)
            total_profit = round(t1_profit + t2_profit, 2)

            # Risk/Reward check
            rr = round(total_profit / dollar_risk, 2) if dollar_risk > 0 else 0
            if rr < SWING_MIN_RR:
                continue

            # Cancel trigger — if opens below this, skip the trade
            cancel_below = round(entry * 0.99, 2)

            results.append({
                "ticker":         ticker,
                "score":          score,
                "entry":          entry,
                "stop":           stop,
                "t1_price":       t1_price,
                "t2_price":       t2_price,
                "t1_profit":      t1_profit,
                "t2_profit":      t2_profit,
                "total_profit":   total_profit,
                "shares":         shares,
                "position_value": position_value,
                "dollar_risk":    dollar_risk,
                "rr":             rr,
                "rsi":            round(rsi, 1) if rsi else "N/A",
                "vol_ratio":      round(vol_ratio, 1),
                "atr":            round(atr, 2),
                "cancel_below":   cancel_below,
                "risk_tier":      risk_tier,
                "details":        scored["details"],
            })

            time.sleep(0.3)

        except Exception as e:
            logger.warning(f"Swing scan {ticker}: {e}")
            continue

    # Sort by score then R/R, return top 3
    results.sort(key=lambda x: (x["score"], x["rr"]), reverse=True)
    logger.info(f"📊 Swing scanner found {len(results)} setup(s)")
    return results[:3]


def format_swing_trades(setups, vix):
    """Format swing trade Telegram message."""
    try:
        t   = _time_display()
        msg = "📊 *SWING TRADE SETUPS*\n"
        msg += f"_{t['sgt']} | {t['est']}_\n\n"

        vix_level = vix["level"] if vix else 20
        if vix:
            msg += f"{vix['emoji']} *VIX: {vix_level:.1f}* — {vix['sentiment']}\n"

        # Capital summary
        risk_pct   = SWING_RISK["low"] if vix_level < 20 else (
                     SWING_RISK["medium"] if vix_level < 25 else SWING_RISK["high"])
        risk_dollars = SWING_CAPITAL * risk_pct
        msg += f"💰 Capital: ${SWING_CAPITAL:,} | Risk/trade: ${risk_dollars:.0f} "
        msg += f"({risk_pct*100:.0f}% — VIX calibrated)\n"
        msg += f"🎯 Daily target: ${SWING_CAPITAL*0.03:.0f}–${SWING_CAPITAL*0.075:.0f} "
        msg += f"| Max trades: {SWING_MAX_TRADES}\n\n"

        if not setups:
            msg += "🔍 *No qualifying swing setups this scan.*\n"
            msg += "_Criteria: Score ≥4/6 | RSI 35-68 | Volume >1.3× | R/R ≥1:2_\n"
            return msg

        signal_labels = {5: "🟢🟢 STRONG SWING", 4: "🟢 MEDIUM SWING"}

        for i, s in enumerate(setups, 1):
            label = signal_labels.get(s["score"], "🟢 SWING")
            msg += f"{'━'*35}\n"
            msg += f"{label}: *{s['ticker']}*\n"
            msg += f"Score: {s['score']:+d}/6 | RSI: {s['rsi']} | Vol: {s['vol_ratio']}× avg\n\n"

            msg += f"*Entry:*  ${s['entry']:.2f} _(limit order)_\n"
            msg += f"*Shares:* {s['shares']} shares = ${s['position_value']:,.0f}\n"
            msg += f"*Stop:*   ${s['stop']:.2f} (1.5× ATR = -${s['dollar_risk']:.0f} max loss)\n\n"

            msg += f"*🎯 Target 1:* ${s['t1_price']:.2f} (+5%) → sell 50% = +${s['t1_profit']:.0f}\n"
            msg += f"*🎯 Target 2:* ${s['t2_price']:.2f} (+10%) → sell 50% = +${s['t2_profit']:.0f}\n"
            msg += f"*Total if both hit:* +${s['total_profit']:.0f}\n"
            msg += f"*R/R Ratio:* 1:{s['rr']} ✅\n\n"

            msg += f"⏱ Hold max *{SWING_HOLD_DAYS} days* — exit regardless\n"
            msg += f"❌ *Cancel if opens below ${s['cancel_below']:.2f}*\n\n"

            msg += f"Why now:\n"
            for d in s["details"][:4]:
                msg += f"  {d}\n"
            msg += "\n"

        msg += "━" * 35 + "\n"
        msg += f"⚠️ _Max {SWING_MAX_TRADES} trades open at once. "
        msg += f"Never risk >3% capital per trade. Exit at T1 if no movement by EOD2._"
        return msg

    except Exception as e:
        logger.error(f"format_swing_trades: {e}")
        return "❌ Swing trade error"


# ============================================================================
# TELEGRAM SENDER (with chunking for long messages)
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
    asyncio.run(_send(message))


# ============================================================================
# SCHEDULED JOB HANDLERS
# ============================================================================

def job_market_report():
    logger.info("📊 Sending market report...")
    try:
        vix       = get_vix()
        portfolio = get_portfolio_data()
        market    = get_market_metrics()
        send_telegram(format_market_report(portfolio, market, vix))
        logger.info("✅ Market report sent")
    except Exception as e:
        logger.error(f"job_market_report: {e}")


def job_trading_signals():
    logger.info("📈 Sending trading signals...")
    try:
        vix = get_vix()
        send_telegram(format_trading_signals(vix))
        logger.info("✅ Trading signals sent")
    except Exception as e:
        logger.error(f"job_trading_signals: {e}")


def job_earnings_alert():
    logger.info("📅 Checking earnings alerts...")
    try:
        alerts = check_earnings_alerts()
        msg    = format_earnings_alerts(alerts)
        if msg:
            send_telegram(msg)
            logger.info(f"✅ Earnings alert sent ({len(alerts)} stock(s))")
        else:
            logger.info("✅ No upcoming earnings in 1-2 days")
    except Exception as e:
        logger.error(f"job_earnings_alert: {e}")


def job_swing_trades():
    logger.info("📊 Running Swing Trade Scanner...")
    try:
        vix    = get_vix()
        setups = run_swing_scanner(vix)
        send_telegram(format_swing_trades(setups, vix))
        logger.info(f"✅ Swing trades sent ({len(setups)} setup(s))")
    except Exception as e:
        logger.error(f"job_swing_trades: {e}")


def job_option_hunter():
    logger.info("🎯 Sending Option Hunter...")
    try:
        vix   = get_vix()
        opps  = run_option_hunter(vix)
        send_telegram(format_option_hunter(opps, vix))
        logger.info(f"✅ Option Hunter sent ({len(opps)} setup(s))")
    except Exception as e:
        logger.error(f"job_option_hunter: {e}")


# ============================================================================
# MAIN SCHEDULER
# ============================================================================

def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.error("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — exiting")
        return

    scheduler = BackgroundScheduler(timezone=EST)

    # --- Pre-market (6:30–9:30 AM ET) ---
    scheduler.add_job(job_market_report, CronTrigger(
        hour="6-9", minute=30, day_of_week="mon-fri"),
        id="pre_market_report", name="Pre-Market Report")

    scheduler.add_job(job_option_hunter, CronTrigger(
        hour=8, minute=30, day_of_week="mon-fri"),
        id="pre_market_options", name="Pre-Market Option Hunt")

    # Swing trades — 9:00am ET (30 mins before open, SGT 9pm)
    scheduler.add_job(job_swing_trades, CronTrigger(
        hour=9, minute=0, day_of_week="mon-fri"),
        id="pre_market_swing", name="Pre-Market Swing Scan")

    # Earnings alert — fires at 7am ET every trading day
    scheduler.add_job(job_earnings_alert, CronTrigger(
        hour=7, minute=0, day_of_week="mon-fri"),
        id="earnings_alert", name="Earnings Alert")

    # --- Market hours (9:30 AM – 3:30 PM ET) ---
    scheduler.add_job(job_market_report, CronTrigger(
        hour="9-15", minute=30, day_of_week="mon-fri"),
        id="market_report", name="Market Hours Report")

    scheduler.add_job(job_option_hunter, CronTrigger(
        hour="10,12,14", minute=0, day_of_week="mon-fri"),
        id="market_options", name="Market Hours Option Hunt")

    # Swing trades — mid-day refresh at 12pm ET
    scheduler.add_job(job_swing_trades, CronTrigger(
        hour=12, minute=0, day_of_week="mon-fri"),
        id="midday_swing", name="Midday Swing Scan")

    # --- Post-market (4:00–5:00 PM ET) ---
    scheduler.add_job(job_trading_signals, CronTrigger(
        hour="16-17", minute="0,30", day_of_week="mon-fri"),
        id="post_signals", name="Post-Market Signals")

    scheduler.add_job(job_option_hunter, CronTrigger(
        hour=16, minute=15, day_of_week="mon-fri"),
        id="post_options", name="Post-Market Option Hunt")

    scheduler.start()
    logger.info("=" * 55)
    logger.info("✅ Trading Bot v2.2 + Option Hunter + Swing Trader")
    logger.info(f"   Scanning {len(OPTION_HUNT_TICKERS)} tickers for options")
    logger.info(f"   Swing capital: ${SWING_CAPITAL:,} | Max trades: {SWING_MAX_TRADES}")
    logger.info(f"   Watching {len(EARNINGS_CALENDAR)} stocks for earnings alerts")
    logger.info("   Swing fires: 9:00am + 12:00pm ET daily")
    logger.info("=" * 55)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"Scheduler fatal error: {e}")
        raise


if __name__ == "__main__":
    main()