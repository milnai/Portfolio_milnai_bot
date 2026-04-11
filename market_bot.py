#!/usr/bin/env python3
"""
Telegram Market Bot - Hourly Stock & Market Updates
Uses Finnhub API for live, reliable stock data
"""

import asyncio
import os
import logging
from datetime import datetime
import requests
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================

# Your Holdings (all positions)
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

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================================
# DATA FETCHING (Finnhub API)
# ============================================================================

def get_portfolio_data():
    """Get current portfolio data from Finnhub"""
    try:
        if not FINNHUB_API_KEY:
            logger.error("❌ FINNHUB_API_KEY not set!")
            return {"positions": {}, "total_value": 0, "total_pnl": 0, "total_pnl_pct": 0}
        
        total_cost = 0
        total_value = 0
        positions = {}
        
        for ticker, info in HOLDINGS.items():
            try:
                logger.info(f"📊 Fetching {ticker}...")
                
                # Retry logic
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        # Finnhub quote endpoint
                        url = f"{FINNHUB_BASE_URL}/quote"
                        params = {"symbol": ticker, "token": FINNHUB_API_KEY}
                        response = requests.get(url, params=params, timeout=10)
                        
                        if response.status_code != 200:
                            raise Exception(f"HTTP {response.status_code}")
                        
                        data = response.json()
                        
                        # Check if we got valid data
                        if "c" not in data or data["c"] is None or data["c"] == 0:
                            logger.warning(f"⚠️ No data for {ticker}")
                            break
                        
                        current_price = float(data["c"])
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
                        logger.info(f"✅ {ticker}: ${current_price:.2f} | PnL: ${pnl:.2f} ({pnl_pct:.2f}%)")
                        break  # Success
                        
                    except Exception as e:
                        if attempt < max_retries - 1:
                            time.sleep(2 ** attempt)  # Exponential backoff
                            logger.info(f"⏳ Retrying {ticker} (attempt {attempt + 2}/{max_retries})")
                        else:
                            logger.error(f"❌ Error fetching {ticker} after {max_retries} retries: {str(e)[:100]}")
                
            except Exception as e:
                logger.error(f"❌ Error processing {ticker}: {str(e)[:100]}")
                continue
        
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        
        logger.info(f"📈 Portfolio complete: {len(positions)} positions fetched | Total value: ${total_value:.2f}")
        
        return {
            "positions": positions,
            "total_value": total_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
        }
    except Exception as e:
        logger.error(f"💥 Error in get_portfolio_data: {str(e)[:100]}")
        return {"positions": {}, "total_value": 0, "total_pnl": 0, "total_pnl_pct": 0}


def get_market_metrics():
    """Get market metrics from Finnhub"""
    try:
        if not FINNHUB_API_KEY:
            logger.error("❌ FINNHUB_API_KEY not set!")
            return {}
        
        metrics = {}
        
        for ticker in MARKET_TICKERS:
            try:
                logger.info(f"📊 Fetching market ticker {ticker}...")
                
                # Retry logic
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        url = f"{FINNHUB_BASE_URL}/quote"
                        params = {"symbol": ticker, "token": FINNHUB_API_KEY}
                        response = requests.get(url, params=params, timeout=10)
                        
                        if response.status_code != 200:
                            raise Exception(f"HTTP {response.status_code}")
                        
                        data = response.json()
                        
                        if "c" not in data or data["c"] is None or data["c"] == 0:
                            logger.warning(f"⚠️ No data for {ticker}")
                            break
                        
                        current = float(data["c"])
                        prev_close = float(data.get("pc", current))  # pc = previous close
                        change_pct = ((current - prev_close) / prev_close * 100) if prev_close > 0 else 0
                        
                        metrics[ticker] = {
                            "price": current,
                            "change_pct": change_pct,
                        }
                        logger.info(f"✅ {ticker}: ${current:.2f} ({change_pct:+.2f}%)")
                        break  # Success
                        
                    except Exception as e:
                        if attempt < max_retries - 1:
                            time.sleep(2 ** attempt)
                            logger.info(f"⏳ Retrying {ticker} (attempt {attempt + 2}/{max_retries})")
                        else:
                            logger.error(f"❌ Error fetching {ticker} after {max_retries} retries: {str(e)[:100]}")
                
            except Exception as e:
                logger.error(f"❌ Error processing {ticker}: {str(e)[:100]}")
                continue
        
        logger.info(f"📈 Market metrics complete: {len(metrics)} tickers fetched")
        return metrics
    except Exception as e:
        logger.error(f"💥 Error in get_market_metrics: {str(e)[:100]}")
        return {}

# ============================================================================
# MESSAGE FORMATTING
# ============================================================================

def format_message(portfolio, metrics):
    """Format the market report message"""
    try:
        msg = "🕐 **Market Update**\n"
        now = datetime.now().strftime('%Y-%m-%d %H:%M UTC')
        msg += f"__{now}__\n\n"
        
        # Market metrics
        msg += "📊 **MARKET METRICS**\n"
        if metrics and len(metrics) > 0:
            for ticker, data in metrics.items():
                emoji = "🟢" if data['change_pct'] >= 0 else "🔴"
                msg += f"{emoji} {ticker}: ${data['price']:.2f} ({data['change_pct']:+.2f}%)\n"
        else:
            msg += "_(No market data)_\n"
        msg += "\n"
        
        # Portfolio
        msg += "💼 **PORTFOLIO**\n"
        if portfolio.get("positions", {}) and len(portfolio["positions"]) > 0:
            for ticker, data in portfolio["positions"].items():
                emoji = "🟢" if data['pnl_pct'] >= 0 else "🔴"
                msg += f"{emoji} {ticker}: ${data['price']:.2f} | PnL: {data['pnl']:+.2f} ({data['pnl_pct']:+.2f}%)\n"
            
            msg += "\n" + "="*40 + "\n"
            total_emoji = "🟢" if portfolio['total_pnl_pct'] >= 0 else "🔴"
            msg += f"{total_emoji} **TOTAL P&L:** ${portfolio['total_pnl']:+.2f} ({portfolio['total_pnl_pct']:+.2f}%)\n"
        else:
            msg += "_(No portfolio data)_\n"
        
        return msg
    except Exception as e:
        logger.error(f"❌ Error formatting message: {e}")
        return "❌ Error formatting report"

# ============================================================================
# TELEGRAM BOT
# ============================================================================

def send_hourly_report():
    """Send hourly market report"""
    try:
        logger.info("📊 Fetching portfolio data...")
        portfolio = get_portfolio_data()
        
        logger.info("📈 Fetching market metrics...")
        metrics = get_market_metrics()
        
        logger.info("📝 Formatting message...")
        message = format_message(portfolio, metrics)
        
        logger.info(f"📤 Sending Telegram message...")
        # Run the async send in a new event loop
        asyncio.run(_send_telegram(message))
        logger.info(f"✅ Sent hourly report!")
        
    except Exception as e:
        logger.error(f"❌ Failed to send report: {e}")


async def _send_telegram(message):
    """Send message via Telegram (async)"""
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))
    await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")

# ============================================================================
# SCHEDULER
# ============================================================================

def main():
    """Main scheduler loop"""
    try:
        scheduler = BackgroundScheduler()
        
        # Add hourly report job
        scheduler.add_job(
            send_hourly_report,
            CronTrigger(minute=0),  # Every hour at :00
            id="hourly_report",
            name="Hourly Market Report"
        )
        
        scheduler.start()
        logger.info("✅ Scheduler started. Sending reports hourly")
        
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
