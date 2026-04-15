#!/usr/bin/env python3
"""
Advanced Trading Bot - Technical Signals with Actionable Trading Levels
- Entry/Exit prices with stop loss
- Profit targets at multiple levels
- Risk/Reward ratios
- VIX-based urgency
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

# Trading Rules
POSITION_SIZE = 10  # shares per trade
STOP_LOSS_PCT = 0.02  # 2% below entry
PROFIT_TARGETS = [
    {"shares": 3, "profit_per_share": 2.00},    # 3 shares at +$2
    {"shares": 4, "profit_per_share": 4.00},    # 4 shares at +$4
    {"shares": 3, "profit_per_share": 6.00},    # 3 shares at +$6
]

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
# DATA FETCHING
# ============================================================================

def get_stock_data(ticker):
    """Get current price and historical data"""
    try:
        if not FINNHUB_API_KEY:
            logger.error("❌ FINNHUB_API_KEY not set!")
            return None
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Current quote
                url = f"{FINNHUB_BASE_URL}/quote"
                params = {"symbol": ticker, "token": FINNHUB_API_KEY}
                response = requests.get(url, params=params, timeout=10)
                
                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")
                
                data = response.json()
                
                if "c" not in data or data["c"] is None or data["c"] == 0:
                    return None
                
                current_price = float(data["c"])
                prev_close = float(data.get("pc", current_price))
                daily_change = current_price - prev_close
                daily_change_pct = (daily_change / prev_close * 100) if prev_close > 0 else 0
                
                # Historical candles
                url = f"{FINNHUB_BASE_URL}/stock/candle"
                params = {
                    "symbol": ticker,
                    "resolution": "D",
                    "count": 200,
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
                    return None
    
    except Exception as e:
        return None


def get_vix_sentiment():
    """Get VIX level and sentiment"""
    try:
        if not FINNHUB_API_KEY:
            return None
        
        url = f"{FINNHUB_BASE_URL}/quote"
        params = {"symbol": "^VIX", "token": FINNHUB_API_KEY}
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if "c" in data and data["c"]:
                vix = float(data["c"])
                
                if vix < 15:
                    sentiment = "😌 **Calm & Confident** - Investors relaxed"
                    emoji = "🟢"
                elif vix < 20:
                    sentiment = "😐 **Normal** - Average conditions"
                    emoji = "🟡"
                elif vix < 25:
                    sentiment = "😟 **Getting Nervous** - Watch for dips"
                    emoji = "🟠"
                elif vix < 35:
                    sentiment = "😰 **Fearful** - STRONG opportunity zone"
                    emoji = "🔴"
                else:
                    sentiment = "😱 **PANIC MODE** - BEST buying time!"
                    emoji = "🔴🔴"
                
                return {
                    "level": vix,
                    "sentiment": sentiment,
                    "emoji": emoji,
                    "is_fearful": vix > 25,
                }
    except:
        pass
    
    return None


# ============================================================================
# TECHNICAL ANALYSIS
# ============================================================================

def calculate_rsi(closes, period=14):
    """Calculate RSI"""
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
        return np.mean(closes)
    except:
        return None


def generate_trading_signal(data):
    """Generate trading signal with entry/exit prices and profit targets"""
    try:
        ticker = data["ticker"]
        current = data["current_price"]
        daily_pct = data["daily_change_pct"]
        closes = data["closes"]
        volumes = data["volumes"]
        
        rsi = calculate_rsi(closes)
        bb = calculate_bollinger_bands(closes)
        ma200 = calculate_ma(closes, 200)
        
        # Determine signal type (BUY or SELL)
        buy_signals = 0
        sell_signals = 0
        signal_details = []
        
        # RSI signals
        if rsi:
            if rsi < 30:
                buy_signals += 1
                signal_details.append("❄️ Stock hit the floor (RSI oversold)")
            elif rsi > 70:
                sell_signals += 1
                signal_details.append("🔥 Stock running too hot (RSI overbought)")
        
        # Bollinger Bands signals
        if bb:
            if current < bb["lower"]:
                buy_signals += 1
                signal_details.append("📉 Touching the bottom (BB lower)")
            elif current > bb["upper"]:
                sell_signals += 1
                signal_details.append("📈 Hit the ceiling (BB upper)")
        
        # 200-day MA signals
        if ma200:
            if current < ma200:
                sell_signals += 1
                signal_details.append("⬇️ Below long-term trend")
            elif current > ma200:
                buy_signals += 1
                signal_details.append("✅ Above long-term support")
        
        # Volume signals
        if len(volumes) > 1:
            today_volume = volumes[-1]
            avg_volume = np.mean(volumes[-20:])
            volume_ratio = today_volume / avg_volume if avg_volume > 0 else 1
            
            if volume_ratio > 1.5:
                if daily_pct < 0:
                    sell_signals += 1
                    signal_details.append("📊 Heavy volume on red day")
                else:
                    buy_signals += 1
                    signal_details.append("📊 Heavy volume on green day")
        
        # Determine action
        signal_type = None
        entry_price = None
        exit_price = None
        
        if buy_signals >= 2:
            signal_type = "BUY"
            entry_price = bb["lower"] if bb else current * 0.98  # Buy at BB lower or 2% below
            
        elif sell_signals >= 2:
            signal_type = "SELL"
            exit_price = bb["upper"] if bb else current * 1.02  # Sell at BB upper or 2% above
        
        return {
            "ticker": ticker,
            "current": current,
            "daily_pct": daily_pct,
            "signal_type": signal_type,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "rsi": rsi,
            "bb": bb,
            "ma200": ma200,
            "signals": signal_details,
        }
    
    except Exception as e:
        logger.error(f"❌ Error generating signal: {str(e)[:100]}")
        return None


# ============================================================================
# TRADING ORDER GENERATION
# ============================================================================

def generate_buy_order(signal, vix_is_fearful):
    """Generate a BUY trading order with entry/exit and profit targets"""
    ticker = signal["ticker"]
    current = signal["current"]
    entry = signal["entry_price"]
    stop_loss = entry * (1 - STOP_LOSS_PCT)
    
    # Build profit targets
    pt_lines = []
    total_profit = 0
    total_shares = 0
    
    for i, target in enumerate(PROFIT_TARGETS):
        shares = target["shares"]
        profit_per_share = target["profit_per_share"]
        exit_price = entry + profit_per_share
        profit = shares * profit_per_share
        
        total_shares += shares
        total_profit += profit
        
        pt_lines.append(f"   • Sell {shares} @ ${exit_price:.2f} (${profit_per_share:+.2f}/share)")
    
    # Risk/Reward
    risk = (entry - stop_loss) * POSITION_SIZE
    reward = total_profit
    rr_ratio = reward / risk if risk > 0 else 0
    
    # Build message
    msg = "🟢 **RECOMMENDED BUY**\n"
    if vix_is_fearful:
        msg = "🔥 **HIGHLY RECOMMENDED BUY** - Market is fearful!\n"
    
    msg += f"\n**{ticker}**\n"
    msg += f"Current Price: ${current:.2f}\n"
    msg += f"Entry Price: ${entry:.2f}\n"
    msg += f"Position: BUY {POSITION_SIZE} shares @ ${entry:.2f} = ${entry*POSITION_SIZE:,.0f}\n\n"
    
    msg += f"🛑 **Stop Loss: ${stop_loss:.2f}** (cut losses at -2%)\n\n"
    
    msg += f"🎯 **Profit Targets (sell in stages):**\n"
    for line in pt_lines:
        msg += f"{line}\n"
    
    msg += f"\n📊 **Risk/Reward: 1 : {rr_ratio:.2f}**\n"
    
    return msg


def generate_sell_order(signal):
    """Generate a SELL order to protect profits"""
    ticker = signal["ticker"]
    current = signal["current"]
    exit_price = signal["exit_price"]
    take_loss = current * (1 - STOP_LOSS_PCT)
    
    msg = "🔴 **PREPARE TO SELL** - Stock is overbought\n\n"
    msg += f"**{ticker}**\n"
    msg += f"Current Price: ${current:.2f}\n"
    msg += f"Sell Price: ${exit_price:.2f}\n"
    msg += f"Position: SELL {POSITION_SIZE} shares @ ${exit_price:.2f} = ${exit_price*POSITION_SIZE:,.0f}\n\n"
    msg += f"⚠️ **Stop Loss if drops to: ${take_loss:.2f}** (cut if bearish reversal)\n"
    msg += f"\n📊 This is peak profit zone - take gains here!\n"
    
    return msg


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
    """Format market update with portfolio"""
    try:
        times = get_time_display()
        
        msg = "🕐 **MARKET SNAPSHOT**\n"
        msg += f"__{times['sgt']} (SGT) | {times['est']} (EST)__\n\n"
        
        if vix:
            msg += f"{vix['emoji']} **VIX: {vix['level']:.2f}**\n"
            msg += f"{vix['sentiment']}\n\n"
        
        msg += "📊 **MARKET INDICES**\n"
        if market_metrics:
            for ticker, data in market_metrics.items():
                emoji = "🟢" if data["daily_pct"] >= 0 else "🔴"
                msg += f"{emoji} {ticker}: ${data['current']:.2f} ({data['daily_pct']:+.2f}%)\n"
        msg += "\n"
        
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
        return "❌ Error"


def format_trading_signals(vix):
    """Format trading orders with entry/exit prices"""
    try:
        times = get_time_display()
        
        msg = "📈 **TRADING SIGNALS**\n"
        msg += f"__{times['sgt']} (SGT) | {times['est']} (EST)__\n\n"
        
        if vix:
            msg += f"{vix['emoji']} **VIX: {vix['level']:.2f}** - {vix['sentiment']}\n\n"
        
        buy_orders = []
        sell_orders = []
        
        # Analyze all holdings
        for ticker in HOLDINGS.keys():
            data = get_stock_data(ticker)
            if data:
                signal = generate_trading_signal(data)
                if signal and signal["signal_type"]:
                    if signal["signal_type"] == "BUY":
                        buy_orders.append((signal, vix and vix["is_fearful"]))
                    elif signal["signal_type"] == "SELL":
                        sell_orders.append(signal)
        
        # Format BUY orders
        if buy_orders:
            for signal, fearful in buy_orders:
                msg += generate_buy_order(signal, fearful) + "\n\n"
        else:
            msg += "🟢 **BUY OPPORTUNITIES:** None right now\n\n"
        
        msg += "="*45 + "\n"
        
        # Format SELL orders
        if sell_orders:
            for signal in sell_orders:
                msg += generate_sell_order(signal) + "\n\n"
        else:
            msg += "🔴 **SELL SIGNALS:** None right now\n"
        
        return msg
    except Exception as e:
        logger.error(f"❌ Error formatting signals: {e}")
        return "❌ Error"


# ============================================================================
# PORTFOLIO
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
    except:
        return None


# ============================================================================
# TELEGRAM BOT
# ============================================================================

def send_market_report():
    """Send market snapshot"""
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
        logger.error(f"❌ Failed: {e}")


def send_trading_signals():
    """Send trading orders with entry/exit prices"""
    try:
        logger.info("📈 Analyzing trading signals...")
        vix = get_vix_sentiment()
        message = format_trading_signals(vix)
        
        logger.info("📤 Sending trading signals...")
        asyncio.run(_send_telegram(message))
        logger.info("✅ Sent trading signals!")
    except Exception as e:
        logger.error(f"❌ Failed: {e}")


async def _send_telegram(message):
    """Send message via Telegram"""
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))
    await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")

# ============================================================================
# SCHEDULER
# ============================================================================

def main():
    """Main scheduler"""
    try:
        scheduler = BackgroundScheduler(timezone=EST)
        
        # Pre-market: hourly
        scheduler.add_job(
            send_market_report,
            CronTrigger(hour='6-9', minute=30, day_of_week='mon-fri'),
            id="pre_market",
            name="Pre-Market"
        )
        
        # Market hours: hourly
        scheduler.add_job(
            send_market_report,
            CronTrigger(hour='9-16', minute=30, day_of_week='mon-fri'),
            id="market_hours",
            name="Market Hours"
        )
        
        # Post-market: every 30 mins with trading signals
        scheduler.add_job(
            send_trading_signals,
            CronTrigger(hour='16-17', minute='0,30', day_of_week='mon-fri'),
            id="post_market",
            name="Trading Signals"
        )
        
        scheduler.start()
        logger.info("✅ Trading Bot Started - US EST Timing Active")
        
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
