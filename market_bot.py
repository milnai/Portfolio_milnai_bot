#!/usr/bin/env python3
"""
Telegram Market Bot - Hourly Stock & Market Updates
Tracks holdings, generates trading signals, reports market metrics via Telegram
Designed for Railway deployment with real-time data pulls every hour
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import asyncio

import yfinance as yf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
import numpy as np
import pandas as pd


# ============================================================================
# CONFIGURATION
# ============================================================================

# Your Holdings (customize here)
HOLDINGS = {
    "RKLB": {"shares": 100, "avg_cost": 15.50, "conviction": "high"},
    "NVDA": {"shares": 50, "avg_cost": 128.00, "conviction": "high"},
    "MSFT": {"shares": 30, "avg_cost": 380.00, "conviction": "high"},
    "ALAB": {"shares": 75, "avg_cost": 35.00, "conviction": "medium"},
    "SCHD": {"shares": 200, "avg_cost": 55.00, "conviction": "low"},
    "INTC": {"shares": 40, "avg_cost": 28.00, "conviction": "medium"},
}

# Market Context Tickers
MARKET_TICKERS = ["SPY", "QQQ", "DIA", "VIX"]

# Trading Thresholds (based on your 6-indicator system)
SIGNAL_THRESHOLDS = {
    "strong_buy": 5,      # 5-6 indicators aligned
    "buy": 4,             # 4 indicators aligned
    "sell": -4,           # 4+ indicators bearish
    "strong_sell": -5,    # 5-6 indicators bearish
}

# Volume & Volatility Thresholds
VOLUME_SPIKE_RATIO = 1.8  # 1.8x 20-day average = spike
VOLATILITY_THRESHOLD = 0.03  # 3% move = notable

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ============================================================================
# TECHNICAL INDICATOR CALCULATIONS
# ============================================================================

def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average"""
    return prices.ewm(span=period, adjust=False).mean()


def calculate_rsi(prices: pd.Series, period: int = 14) -> float:
    """Calculate RSI for latest bar"""
    deltas = np.diff(prices.values)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi_values = [100. - 100. / (1. + rs)]
    
    for delta in deltas[period+1:]:
        if delta >= 0:
            up = (up * (period - 1) + delta) / period
            down = (down * (period - 1)) / period
        else:
            up = (up * (period - 1)) / period
            down = (down * (period - 1) - delta) / period
        rs = up / down if down != 0 else 0
        rsi_values.append(100. - 100. / (1. + rs))
    
    return rsi_values[-1] if rsi_values else 50


def calculate_macd(prices: pd.Series) -> Tuple[float, float, float]:
    """Calculate MACD, Signal, Histogram"""
    ema_12 = calculate_ema(prices, 12)
    ema_26 = calculate_ema(prices, 26)
    macd = ema_12 - ema_26
    signal = calculate_ema(macd, 9)
    histogram = macd - signal
    
    return float(macd.iloc[-1]), float(signal.iloc[-1]), float(histogram.iloc[-1])


def calculate_bollinger_bands(prices: pd.Series, period: int = 20, std_dev: int = 2) -> Tuple[float, float, float]:
    """Calculate Bollinger Bands"""
    sma = prices.rolling(period).mean()
    std = prices.rolling(period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    
    return float(upper.iloc[-1]), float(sma.iloc[-1]), float(lower.iloc[-1])


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Simplified ADX calculation"""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    
    plus_dm = plus_dm.where(plus_dm > minus_dm, 0).where(plus_dm > 0, 0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0).where(minus_dm > 0, 0)
    
    tr = pd.DataFrame({
        'hl': high - low,
        'hc': abs(high - close.shift(1)),
        'lc': abs(low - close.shift(1))
    }).max(axis=1)
    
    atr = tr.rolling(period).mean()
    plus_di = (plus_dm.rolling(period).mean() / atr) * 100
    minus_di = (minus_dm.rolling(period).mean() / atr) * 100
    
    adx_raw = abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = adx_raw.rolling(period).mean() * 100
    
    return float(adx.iloc[-1]) if len(adx) > 0 and not np.isnan(adx.iloc[-1]) else 0


def calculate_signal_score(ticker: str, period: int = 60) -> Dict:
    """
    Calculate 6-indicator signal score for a stock.
    Returns: score (-6 to +6), individual signals, confidence
    """
    try:
        # Fetch data (60 days for indicators + volume)
        data = yf.download(ticker, period="3mo", progress=False, prepost=False)
        if data.empty or len(data) < 30:
            return {"error": f"Insufficient data for {ticker}"}
        
        close = data['Close']
        high = data['High']
        low = data['Low']
        volume = data['Volume']
        
        # 1. EMA Trend
        ema20 = calculate_ema(close, 20)
        ema50 = calculate_ema(close, 50)
        ema_signal = 1 if ema20.iloc[-1] > ema50.iloc[-1] else (-1 if ema20.iloc[-1] < ema50.iloc[-1] else 0)
        
        # 2. RSI
        rsi = calculate_rsi(close.values)
        if rsi < 30:
            rsi_signal = 1
        elif rsi > 70:
            rsi_signal = -1
        else:
            rsi_signal = 0
        
        # 3. MACD
        macd, signal_line, histogram = calculate_macd(close)
        macd_signal = 1 if macd > signal_line else (-1 if macd < signal_line else 0)
        
        # 4. Bollinger Bands
        upper_bb, mid_bb, lower_bb = calculate_bollinger_bands(close)
        current_price = close.iloc[-1]
        if current_price <= lower_bb:
            bb_signal = 1
        elif current_price >= upper_bb:
            bb_signal = -1
        else:
            bb_signal = 0
        
        # 5. Volume
        vol_ratio = volume.iloc[-1] / volume.iloc[-20:].mean()
        if vol_ratio >= 1.8:
            vol_signal = 1 if ema_signal > 0 else -1
        elif vol_ratio >= 1.5:
            vol_signal = 0.5 if ema_signal > 0 else -0.5
        else:
            vol_signal = 0
        
        # 6. ADX Trend Strength
        adx = calculate_adx(high, low, close)
        if adx > 25:
            adx_signal = 1 if ema_signal > 0 else (-1 if ema_signal < 0 else 0)
        else:
            adx_signal = 0
        
        # Calculate total score
        total_score = ema_signal + rsi_signal + macd_signal + bb_signal + vol_signal + adx_signal
        
        # Calculate confidence (0-100%)
        aligned = sum(1 for s in [ema_signal, rsi_signal, macd_signal, bb_signal, adx_signal] if s != 0)
        confidence = min(100, (aligned / 5) * 100 * (1 + abs(vol_signal) * 0.2))
        
        return {
            "ticker": ticker,
            "score": total_score,
            "confidence": confidence,
            "ema_signal": ema_signal,
            "rsi": rsi,
            "rsi_signal": rsi_signal,
            "macd": macd,
            "macd_signal": macd_signal,
            "bb_upper": upper_bb,
            "bb_mid": mid_bb,
            "bb_lower": lower_bb,
            "current_price": current_price,
            "bb_signal": bb_signal,
            "volume_ratio": vol_ratio,
            "vol_signal": vol_signal,
            "adx": adx,
            "adx_signal": adx_signal,
            "ema20": ema20.iloc[-1],
            "ema50": ema50.iloc[-1],
        }
    
    except Exception as e:
        logger.error(f"Error calculating signals for {ticker}: {e}")
        return {"error": str(e)}


def get_signal_emoji(score: float) -> str:
    """Get emoji based on signal score"""
    if score >= 5:
        return "🟢🟢"
    elif score >= 4:
        return "🟢"
    elif score >= 2:
        return "🟡"
    elif score <= -5:
        return "🔴🔴"
    elif score <= -4:
        return "🔴"
    elif score <= -2:
        return "🟠"
    else:
        return "⏸️"


# ============================================================================
# PORTFOLIO & MARKET DATA
# ============================================================================

def calculate_portfolio_pnl(holdings: Dict) -> Dict:
    """Calculate portfolio P&L"""
    total_cost = 0
    total_value = 0
    positions = {}
    
    for ticker, info in holdings.items():
        try:
            data = yf.download(ticker, period="1d", progress=False)
            if data.empty:
                continue
            
            current_price = data['Close'].iloc[-1]
            shares = info["shares"]
            avg_cost = info["avg_cost"]
            
            cost = shares * avg_cost
            value = shares * current_price
            pnl = value - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0
            
            total_cost += cost
            total_value += value
            
            positions[ticker] = {
                "shares": shares,
                "avg_cost": avg_cost,
                "current_price": current_price,
                "cost": cost,
                "value": value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }
        except Exception as e:
            logger.error(f"Error fetching data for {ticker}: {e}")
            continue
    
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    
    return {
        "positions": positions,
        "total_cost": total_cost,
        "total_value": total_value,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
    }


def get_market_metrics() -> Dict:
    """Get broad market metrics"""
    metrics = {}
    
    for ticker in MARKET_TICKERS:
        try:
            data = yf.download(ticker, period="5d", progress=False)
            if data.empty:
                continue
            
            close = data['Close']
            current = close.iloc[-1]
            prev_close = close.iloc[-2]
            change = current - prev_close
            change_pct = (change / prev_close * 100) if prev_close > 0 else 0
            day_high = data['High'].iloc[-1]
            day_low = data['Low'].iloc[-1]
            
            metrics[ticker] = {
                "price": current,
                "change": change,
                "change_pct": change_pct,
                "day_high": day_high,
                "day_low": day_low,
            }
        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
            continue
    
    return metrics


def detect_volume_spikes(holdings: Dict) -> List[Dict]:
    """Detect volume spikes in holdings"""
    spikes = []
    
    for ticker in holdings.keys():
        try:
            data = yf.download(ticker, period="30d", progress=False)
            if data.empty or len(data) < 20:
                continue
            
            volume = data['Volume']
            current_vol = volume.iloc[-1]
            avg_vol = volume.iloc[-20:].mean()
            vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1
            
            if vol_ratio >= VOLUME_SPIKE_RATIO:
                close = data['Close']
                change_pct = ((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100) if close.iloc[-2] > 0 else 0
                
                spikes.append({
                    "ticker": ticker,
                    "volume_ratio": vol_ratio,
                    "current_vol": int(current_vol),
                    "avg_vol": int(avg_vol),
                    "price_change": change_pct,
                })
        except Exception as e:
            logger.error(f"Error detecting volume spike for {ticker}: {e}")
            continue
    
    return spikes


# ============================================================================
# MESSAGE FORMATTING
# ============================================================================

def format_portfolio_message(portfolio: Dict) -> str:
    """Format portfolio P&L message"""
    if not portfolio.get("positions"):
        return "❌ Unable to fetch portfolio data"
    
    msg = "💼 **PORTFOLIO UPDATE**\n\n"
    
    for ticker, pos in portfolio["positions"].items():
        emoji = "🟢" if pos["pnl"] >= 0 else "🔴"
        msg += f"{emoji} {ticker}: ${pos['current_price']:.2f} "
        msg += f"({pos['pnl_pct']:+.2f}%) | ${pos['pnl']:+.2f}\n"
    
    msg += "\n" + "="*40 + "\n"
    total_emoji = "🟢" if portfolio["total_pnl"] >= 0 else "🔴"
    msg += f"{total_emoji} **Total**: ${portfolio['total_value']:.2f}\n"
    msg += f"P&L: ${portfolio['total_pnl']:+.2f} ({portfolio['total_pnl_pct']:+.2f}%)\n"
    
    return msg


def format_market_message(metrics: Dict) -> str:
    """Format market metrics message"""
    if not metrics:
        return "❌ Unable to fetch market data"
    
    msg = "📊 **MARKET METRICS**\n\n"
    
    for ticker, data in metrics.items():
        emoji = "📈" if data["change"] >= 0 else "📉"
        msg += f"{emoji} {ticker}: ${data['price']:.2f} ({data['change_pct']:+.2f}%)\n"
    
    return msg


def format_signals_message(holdings: Dict) -> str:
    """Format trading signals message"""
    msg = "🎯 **TRADING SIGNALS**\n\n"
    
    for ticker in holdings.keys():
        signal_data = calculate_signal_score(ticker)
        
        if "error" in signal_data:
            msg += f"❌ {ticker}: {signal_data['error']}\n"
            continue
        
        emoji = get_signal_emoji(signal_data["score"])
        msg += f"{emoji} {ticker}: Score {signal_data['score']:.1f}/6 | "
        msg += f"Confidence {signal_data['confidence']:.0f}%\n"
        msg += f"   Price: ${signal_data['current_price']:.2f} | RSI: {signal_data['rsi']:.1f}\n"
    
    return msg


def format_spikes_message(spikes: List[Dict]) -> str:
    """Format volume spike alerts"""
    if not spikes:
        return None
    
    msg = "⚡ **VOLUME SPIKES DETECTED**\n\n"
    for spike in spikes:
        price_emoji = "📈" if spike["price_change"] >= 0 else "📉"
        msg += f"{price_emoji} {spike['ticker']}: {spike['volume_ratio']:.1f}x volume "
        msg += f"({spike['price_change']:+.2f}% price move)\n"
    
    return msg


# ============================================================================
# TELEGRAM BOT
# ============================================================================

async def send_hourly_report():
    """Send comprehensive hourly market report"""
    try:
        bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
        chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))
        
        # Timestamp
        timestamp = datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = f"⏰ **Market Update** — {timestamp}\n\n"
        
        # Check if market is open
        now_utc = datetime.utcnow()
        market_open = now_utc.weekday() < 4  # Mon-Fri
        market_open = market_open and 13 <= now_utc.hour <= 20  # 9:30 AM - 4:00 PM ET
        
        # Get data
        portfolio = calculate_portfolio_pnl(HOLDINGS)
        metrics = get_market_metrics()
        spikes = detect_volume_spikes(HOLDINGS) if market_open else []
        
        # Build message
        message = header
        message += format_market_message(metrics)
        message += "\n"
        message += format_portfolio_message(portfolio)
        message += "\n"
        message += format_signals_message(HOLDINGS)
        
        if spikes:
            message += "\n"
            message += format_spikes_message(spikes)
        
        # Send
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        logger.info(f"✅ Sent hourly report to {chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Failed to send report: {e}")


async def send_market_alert():
    """Send alert for strong signals (only during market hours)"""
    try:
        now_utc = datetime.utcnow()
        market_open = now_utc.weekday() < 4 and 13 <= now_utc.hour <= 20
        
        if not market_open:
            return
        
        bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
        chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))
        
        alerts = []
        for ticker in HOLDINGS.keys():
            signal_data = calculate_signal_score(ticker)
            if "error" not in signal_data:
                score = signal_data["score"]
                confidence = signal_data["confidence"]
                
                # Alert on strong signals
                if score >= 5 or score <= -5:
                    emoji = get_signal_emoji(score)
                    alerts.append(f"{emoji} {ticker}: {score:.1f}/6 (Confidence: {confidence:.0f}%)")
        
        if alerts:
            msg = "🚨 **STRONG SIGNALS DETECTED**\n\n"
            msg += "\n".join(alerts)
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            logger.info(f"✅ Sent alert for {len(alerts)} strong signals")
    
    except Exception as e:
        logger.error(f"❌ Failed to send alert: {e}")


# ============================================================================
# SCHEDULER SETUP
# ============================================================================

async def main():
    """Main scheduler loop"""
    scheduler = AsyncIOScheduler()
    
    # Hourly reports (every hour)
    scheduler.add_job(
        send_hourly_report,
        CronTrigger(minute="*/1"),  # Every hour at :00
        id="hourly_report",
        name="Hourly Market Report"
    )
    
    # Alerts on strong signals (every 30 min during market)
    scheduler.add_job(
        send_market_alert,
        CronTrigger(minute="*/30"),  # Every 30 minutes
        id="market_alert",
        name="Strong Signal Alerts"
    )
    
    scheduler.start()
    logger.info("✅ Scheduler started. Sending reports hourly + alerts every 30min")
    
    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
