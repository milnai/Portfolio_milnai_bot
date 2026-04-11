#!/usr/bin/env python3
"""
Advanced Telegram Market Bot - Layman-Friendly Technical Signals
- Vulnerability signals (when to sell/prepare)
- Opportunity signals (when to buy)
- US EST market timing with SGT display
- Monday-Friday trading week only
"""

import asyncio
import os
import logging
from datetime import datetime
import requests
import time
import pytz
import numpy as np

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================

# Your Holdings
HOLDINGS = {
    "RKLB": {"shares": 62, "avg_cost": 68.152},
    "NVDA": {"shares": 10, "avg_cost": 175.90},
    "MSFT": {"shares": 2, "avg_cost": 372.50},
    "ALAB": {"shares": 3, "avg_cost": 116.00},
    "SCHD": {"shares": 15, "avg_cost": 30.50},
    "INTC": {"shares": 4, "avg_cost": 51.33},
    "NBIS": {"shares": 5, "avg_cost": 114.90},
    "NVDL": {"shares": 10, "avg_cost": 80.90},
    "SLV": {"shares": 18, "avg_cost": 90.667},
    "GRAB": {"shares": 284, "avg_cost": 5.899},
}

MARKET_TICKERS = ["SPY", "QQQ"]

# Finnhub API
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

# Timezones
EST = pytz.timezone('US/Eastern')
SGT = pytz.timezone('Asia/Singapore')

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================================
# DATA FETCHING (Finnhub API)
# ============================================================================

def get_stock_data(ticker):
    """Get current price and historical data for technical analysis"""
    try:
        if not FINNHUB_API_KEY:
            logger.error("❌ FINNHUB_API_KEY not set!")
            return None
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Get current quote
                url = f"{FINNHUB_BASE_URL}/quote"
                params = {"symbol": ticker, "token": FINNHUB_API_KEY}
                response = requests.get(url, params=params, timeout=10)
                
                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")
                
                data = response.json()
                
                if "c" not in data or data["c"] is None or data["c"] == 0:
                    logger.warning(f"⚠️ No data for {ticker}")
                    return None
                
                current_price = float(data["c"])
                prev_close = float(data.get("pc", current_price))
                daily_change = current_price - prev_close
                daily_change_pct = (daily_change / prev_close * 100) if prev_close > 0 else 0
                
                # Get historical candles for technical analysis
                url = f"{FINNHUB_BASE_URL}/stock/candle"
                params = {
                    "symbol": ticker,
                    "resolution": "D",  # Daily
                    "count": 200,  # Last 200 days for MA200
                    "token": FINNHUB_API_KEY
                }
                response = requests.get(url, params=params, timeout=10)
                
                if response.status_code == 200:
                    candles = response.json()
                    if "c" in candles and candles["c"]:
                        closes = candles["c"]
                        volumes = candles.get("v", [])
                        
                        return {
                            "ticker": ticker,
                            "current_price": current_price,
                            "prev_close": prev_close,
                            "daily_change": daily_change,
                            "daily_change_pct": daily_change_pct,
                            "closes": closes,
                            "volumes": volumes,
                        }
                
                return None
                
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"❌ Error fetching {ticker}: {str(e)[:100]}")
                    return None
    
    except Exception as e:
        logger.error(f"❌ Error in get_stock_data: {str(e)[:100]}")
        return None


def calculate_rsi(closes, period=14):
    """Calculate RSI (Relative Strength Index)"""
    try:
        if len(closes) < period + 1:
            return None
        
        closes = np.array(closes[-period-1:], dtype=float)
        deltas = np.diff(closes)
        
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        
        if avg_loss == 0:
            return 100 if avg_gain > 0 else 0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    except:
        return None


def calculate_bollinger_bands(closes, period=20, std_dev=2):
    """Calculate Bollinger Bands"""
    try:
        if len(closes) < period:
            return None
        
        closes = np.array(closes[-period:], dtype=float)
        sma = np.mean(closes)
        std = np.std(closes)
        
        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)
        
        return {"upper": upper, "middle": sma, "lower": lower}
    except:
        return None


def calculate_ma(closes, period=200):
    """Calculate Moving Average"""
    try:
        if len(closes) < period:
            return None
        
        closes = np.array(closes[-period:], dtype=float)
        ma = np.mean(closes)
        
        return ma
    except:
        return None


def get_vix_sentiment():
    """Get VIX level and sentiment"""
    try:
        if not FINNHUB_API_KEY:
            return None
        
        # VIX ticker
        url = f"{FINNHUB_BASE_URL}/quote"
        params = {"symbol": "^VIX", "token": FINNHUB_API_KEY}
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if "c" in data and data["c"]:
                vix = float(data["c"])
                
                # Sentiment analysis
                if vix < 15:
                    sentiment = "😌 **Calm & Confident** - Investors relaxed, be cautious (peak greed)"
                    emoji = "🟢"
                elif vix < 20:
                    sentiment = "😐 **Normal** - Average market conditions"
                    emoji = "🟡"
                elif vix < 25:
                    sentiment = "😟 **Getting Nervous** - Some worry emerging, watch for dips"
                    emoji = "🟠"
                elif vix < 35:
                    sentiment = "😰 **Fearful** - Investors scared, STRONG buying opportunity"
                    emoji = "🔴"
                else:
                    sentiment = "😱 **PANIC MODE** - Extreme fear, MAJOR dip, best buying time!"
                    emoji = "🔴🔴"
                
                return {
                    "level": vix,
                    "sentiment": sentiment,
                    "emoji": emoji,
                    "is_fearful": vix > 25,  # Flag for high VIX
                }
    except Exception as e:
        logger.error(f"❌ Error fetching VIX: {str(e)[:100]}")
    
    return None
    """Analyze technical signals - return buy/sell signals in layman's terms"""
    try:
        ticker = data["ticker"]
        current = data["current_price"]
        daily_pct = data["daily_change_pct"]
        closes = data["closes"]
        volumes = data["volumes"]
        
        vulnerability_signals = []
        opportunity_signals = []
        
        # RSI Analysis
        rsi = calculate_rsi(closes)
        if rsi:
            if rsi > 70:
                vulnerability_signals.append("🔥 Stock running too hot (overbought)")
            elif rsi < 30:
                opportunity_signals.append("❄️ Stock hit the floor (oversold)")
        
        # Bollinger Bands Analysis
        bb = calculate_bollinger_bands(closes)
        if bb:
            if current > bb["upper"]:
                vulnerability_signals.append("📈 Hit the ceiling (stretched too far up)")
            elif current < bb["lower"]:
                opportunity_signals.append("📉 Touching the bottom (sweet spot to buy)")
        
        # 200-day MA Analysis
        ma200 = calculate_ma(closes, 200)
        if ma200:
            if current < ma200:
                vulnerability_signals.append("⬇️ Below long-term trend (downtrend zone)")
            elif current > ma200:
                opportunity_signals.append("✅ Above long-term support (strong base)")
        
        # Daily change Analysis
        if daily_pct < -3:
            vulnerability_signals.append(f"🔴 Big red day ({daily_pct:.2f}%) - Heavy selling")
        elif daily_pct > 3:
            opportunity_signals.append(f"🟢 Big green day ({daily_pct:.2f}%) - Heavy buying")
        
        # Volume Analysis
        if len(volumes) > 1:
            today_volume = volumes[-1]
            avg_volume = np.mean(volumes[-20:])
            volume_ratio = today_volume / avg_volume if avg_volume > 0 else 1
            
            if volume_ratio > 1.5:
                if daily_pct < 0:
                    vulnerability_signals.append("📊 Heavy volume on red day (panic dump)")
                else:
                    opportunity_signals.append("📊 Heavy volume on green day (real buyers)")
        
        return {
            "ticker": ticker,
            "current": current,
            "daily_pct": daily_pct,
            "rsi": rsi,
            "vulnerability": vulnerability_signals,
            "opportunity": opportunity_signals,
        }
    
    except Exception as e:
        logger.error(f"❌ Error analyzing signals: {str(e)[:100]}")
        return None


# ============================================================================
# MESSAGE FORMATTING
# ============================================================================

def get_time_display():
    """Get current time in both EST and SGT"""
    now_est = datetime.now(EST)
    now_sgt = now_est.astimezone(SGT)
    
    return {
        "est": now_est.strftime('%Y-%m-%d %H:%M EST'),
        "sgt": now_sgt.strftime('%Y-%m-%d %H:%M SGT'),
    }


def format_market_report(portfolio, market_metrics, vix):
    """Format market update with portfolio P&L and VIX sentiment"""
    try:
        times = get_time_display()
        
        msg = "🕐 **MARKET SNAPSHOT**\n"
        msg += f"__{times['sgt']} (SGT) | {times['est']} (EST)__\n\n"
        
        # VIX Sentiment
        if vix:
            msg += f"{vix['emoji']} **VIX: {vix['level']:.2f}**\n"
            msg += f"{vix['sentiment']}\n\n"
        
        # Market metrics
        msg += "📊 **MARKET INDICES**\n"
        if market_metrics:
            for ticker, data in market_metrics.items():
                emoji = "🟢" if data["daily_pct"] >= 0 else "🔴"
                msg += f"{emoji} {ticker}: ${data['current']:.2f} ({data['daily_pct']:+.2f}%)\n"
        msg += "\n"
        
        # Portfolio summary
        if portfolio.get("positions", {}):
            msg += "💼 **PORTFOLIO**\n"
            for ticker, pos in portfolio["positions"].items():
                emoji = "🟢" if pos["pnl_pct"] >= 0 else "🔴"
                msg += f"{emoji} {ticker}: ${pos['price']:.2f} | PnL {pos['pnl']:+.0f} ({pos['pnl_pct']:+.1f}%)\n"
            
            msg += "\n" + "="*45 + "\n"
            total_emoji = "🟢" if portfolio["total_pnl_pct"] >= 0 else "🔴"
            msg += f"{total_emoji} **TOTAL P&L: ${portfolio['total_pnl']:+.0f} ({portfolio['total_pnl_pct']:+.1f}%)**\n"
        
        return msg
    except Exception as e:
        logger.error(f"❌ Error formatting market report: {e}")
        return "❌ Error formatting report"


def format_signal_report(vix):
    """Format buy/sell opportunity report with VIX-based recommendations"""
    try:
        times = get_time_display()
        
        msg = "🔍 **TECHNICAL ANALYSIS SIGNALS**\n"
        msg += f"__{times['sgt']} (SGT) | {times['est']} (EST)__\n\n"
        
        # VIX Sentiment
        if vix:
            msg += f"{vix['emoji']} **VIX: {vix['level']:.2f}** - {vix['sentiment']}\n\n"
        
        vulnerable_stocks = []
        opportunity_stocks = []
        
        # Analyze all holdings
        for ticker in HOLDINGS.keys():
            data = get_stock_data(ticker)
            if data:
                signals = analyze_signals(data)
                if signals:
                    if signals["vulnerability"]:
                        vulnerable_stocks.append(signals)
                    if signals["opportunity"]:
                        opportunity_stocks.append(signals)
        
        # Format vulnerability alerts
        if vulnerable_stocks:
            msg += "🔴 **WATCH OUT - VULNERABLE TO DIP**\n"
            for stock in vulnerable_stocks:
                msg += f"\n**{stock['ticker']}** (${stock['current']:.2f}, {stock['daily_pct']:+.2f}%)\n"
                for signal in stock["vulnerability"]:
                    msg += f"  • {signal}\n"
                msg += "  → Consider preparing to sell\n"
        else:
            msg += "🔴 **VULNERABLE STOCKS:** None right now\n"
        
        msg += "\n" + "="*45 + "\n"
        
        # Format opportunity alerts
        if opportunity_stocks:
            msg += "🟢 **BUYING OPPORTUNITIES**\n"
            for stock in opportunity_stocks:
                msg += f"\n**{stock['ticker']}** (${stock['current']:.2f}, {stock['daily_pct']:+.2f}%)\n"
                for signal in stock["opportunity"]:
                    msg += f"  • {signal}\n"
                
                # Mark as HIGHLY RECOMMENDED if VIX is high (fearful market)
                if vix and vix["is_fearful"]:
                    msg += f"  → 🔥 **HIGHLY RECOMMENDED** - VIX is {vix['level']:.0f}, market fearful, strong buy signal!\n"
                else:
                    msg += "  → Consider buying the dip\n"
        else:
            msg += "🟢 **BUYING OPPORTUNITIES:** None right now\n"
        
        return msg
    except Exception as e:
        logger.error(f"❌ Error formatting signal report: {e}")
        return "❌ Error formatting signals"


# ============================================================================
# PORTFOLIO ANALYSIS
# ============================================================================

def get_portfolio_data():
    """Get portfolio P&L"""
    try:
        total_cost = 0
        total_value = 0
        positions = {}
        
        for ticker, info in HOLDINGS.items():
            data = get_stock_data(ticker)
            if data:
                current_price = data["current_price"]
                shares = info["shares"]
                avg_cost = info["avg_cost"]
                
                cost = shares * avg_cost
                value = shares * current_price
                pnl = value - cost
                pnl_pct = (pnl / cost * 100) if cost > 0 else 0
                
                total_cost += cost
                total_value += value
                
                positions[ticker] = {
                    "price": current_price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                }
        
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        
        return {
            "positions": positions,
            "total_value": total_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
        }
    except Exception as e:
        logger.error(f"❌ Error in get_portfolio_data: {str(e)[:100]}")
        return {"positions": {}, "total_value": 0, "total_pnl": 0, "total_pnl_pct": 0}


def get_market_metrics():
    """Get market indices"""
    try:
        metrics = {}
        
        for ticker in MARKET_TICKERS:
            data = get_stock_data(ticker)
            if data:
                metrics[ticker] = {
                    "current": data["current_price"],
                    "daily_pct": data["daily_change_pct"],
                }
        
        return metrics if metrics else None
    except Exception as e:
        logger.error(f"❌ Error in get_market_metrics: {str(e)[:100]}")
        return None


# ============================================================================
# TELEGRAM BOT
# ============================================================================

def send_market_report():
    """Send market snapshot + portfolio"""
    try:
        logger.info("📊 Fetching market data...")
        portfolio = get_portfolio_data()
        market = get_market_metrics()
        vix = get_vix_sentiment()
        
        message = format_market_report(portfolio, market, vix)
        
        logger.info("📤 Sending market report...")
        asyncio.run(_send_telegram(message))
        logger.info("✅ Sent market report!")
    except Exception as e:
        logger.error(f"❌ Failed to send market report: {e}")


def send_signal_report():
    """Send technical signals (buy/sell opportunities)"""
    try:
        logger.info("🔍 Analyzing technical signals...")
        vix = get_vix_sentiment()
        message = format_signal_report(vix)
        
        logger.info("📤 Sending signal report...")
        asyncio.run(_send_telegram(message))
        logger.info("✅ Sent signal report!")
    except Exception as e:
        logger.error(f"❌ Failed to send signal report: {e}")


async def _send_telegram(message):
    """Send message via Telegram (async)"""
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))
    await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")

# ============================================================================
# SCHEDULER (US EST TIMING)
# ============================================================================

def main():
    """Main scheduler with US EST market hours"""
    try:
        scheduler = BackgroundScheduler(timezone=EST)
        
        # Pre-market: 6:30 AM - 9:30 AM EST (every hour)
        scheduler.add_job(
            send_market_report,
            CronTrigger(hour='6-9', minute=30, day_of_week='mon-fri'),
            id="pre_market",
            name="Pre-Market Report"
        )
        
        # Market hours: 9:30 AM - 4:00 PM EST (every hour)
        scheduler.add_job(
            send_market_report,
            CronTrigger(hour='9-16', minute=30, day_of_week='mon-fri'),
            id="market_hours",
            name="Market Hours Report"
        )
        
        # Post-market first 2 hours: 4:00 PM - 6:00 PM EST (every 30 mins)
        scheduler.add_job(
            send_signal_report,
            CronTrigger(hour='16-17', minute='0,30', day_of_week='mon-fri'),
            id="post_market",
            name="Post-Market Signals"
        )
        
        scheduler.start()
        logger.info("✅ Scheduler started - US EST market timing active")
        
        # Keep running
        import time
        while True:
            time.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped")
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"❌ Scheduler error: {e}")
        raise


if __name__ == "__main__":
    main()
