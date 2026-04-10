#!/usr/bin/env python3
"""
Simplified Telegram Market Bot - Hourly Stock & Market Updates
No complex indicator calculations - just portfolio + market metrics
"""

import os
import logging
from datetime import datetime
import asyncio

import yfinance as yf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot


# ============================================================================
# CONFIGURATION
# ============================================================================

# Your Holdings
HOLDINGS = {
    "RKLB": {"shares": 100, "avg_cost": 15.50},
    "NVDA": {"shares": 50, "avg_cost": 128.00},
    "MSFT": {"shares": 30, "avg_cost": 380.00},
    "ALAB": {"shares": 75, "avg_cost": 35.00},
    "SCHD": {"shares": 200, "avg_cost": 55.00},
    "INTC": {"shares": 40, "avg_cost": 28.00},
}

MARKET_TICKERS = ["SPY", "QQQ", "DIA", "VIX"]

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ============================================================================
# DATA FETCHING
# ============================================================================

def get_portfolio_data():
    """Get current portfolio data"""
    try:
        total_cost = 0
        total_value = 0
        positions = {}
        
        for ticker, info in HOLDINGS.items():
            try:
                logger.info(f"Fetching {ticker}...")
                data = yf.download(ticker, period="1d", progress=False, timeout=10)
                
                if data is None or data.empty:
                    logger.warning(f"No data for {ticker}")
                    continue
                
                current_price = float(data['Close'].iloc[-1])
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
                logger.info(f"✅ Got {ticker}: ${current_price:.2f}")
                
            except Exception as e:
                logger.error(f"Error fetching {ticker}: {e}")
                continue
        
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        
        logger.info(f"Portfolio positions: {len(positions)}")
        
        return {
            "positions": positions,
            "total_value": total_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
        }
    except Exception as e:
        logger.error(f"Error in get_portfolio_data: {e}")
        return {"positions": {}, "total_value": 0, "total_pnl": 0, "total_pnl_pct": 0}


def get_market_metrics():
    """Get market metrics"""
    try:
        metrics = {}
        
        for ticker in MARKET_TICKERS:
            try:
                logger.info(f"Fetching {ticker}...")
                data = yf.download(ticker, period="5d", progress=False, timeout=10)
                
                if data is None or data.empty:
                    logger.warning(f"No data for {ticker}")
                    continue
                
                close = data['Close']
                current = float(close.iloc[-1])
                prev_close = float(close.iloc[-2])
                change_pct = ((current - prev_close) / prev_close * 100) if prev_close > 0 else 0
                
                metrics[ticker] = {
                    "price": current,
                    "change_pct": change_pct,
                }
                logger.info(f"✅ Got {ticker}: ${current:.2f}")
                
            except Exception as e:
                logger.error(f"Error fetching {ticker}: {e}")
                continue
        
        logger.info(f"Market metrics: {len(metrics)} tickers")
        return metrics
    except Exception as e:
        logger.error(f"Error in get_market_metrics: {e}")
        return {}


# ============================================================================
# MESSAGE FORMATTING
# ============================================================================

def format_message(portfolio, metrics):
    """Format the complete market report"""
    try:
        msg = "⏰ **Market Update**\n"
        now = datetime.now().strftime('%Y-%m-%d %H:%M UTC')
        msg += f"__{now}__\n\n"
        
        # Market metrics
        msg += "📊 **MARKET METRICS**\n"
        if metrics and len(metrics) > 0:
            for ticker, data in metrics.items():
                emoji = "📈" if data["change_pct"] >= 0 else "📉"
                msg += f"{emoji} {ticker}: ${data['price']:.2f} ({data['change_pct']:+.2f}%)\n"
        else:
            msg += "_(No market data)_\n"
        msg += "\n"
        
        # Portfolio
        msg += "💼 **PORTFOLIO**\n"
        if portfolio and len(portfolio.get("positions", {})) > 0:
            for ticker, pos in portfolio["positions"].items():
                emoji = "🟢" if pos["pnl"] >= 0 else "🔴"
                msg += f"{emoji} {ticker}: ${pos['price']:.2f} ({pos['pnl_pct']:+.2f}%)\n"
            
            msg += "\n" + "="*40 + "\n"
            total_emoji = "🟢" if portfolio["total_pnl"] >= 0 else "🔴"
            msg += f"{total_emoji} **TOTAL P&L**: ${portfolio['total_pnl']:+.2f} "
            msg += f"({portfolio['total_pnl_pct']:+.2f}%)\n"
        else:
            msg += "_(No portfolio data)_\n"
        
        return msg
    except Exception as e:
        logger.error(f"Error formatting message: {e}")
        return "❌ Error formatting report"


# ============================================================================
# TELEGRAM BOT
# ============================================================================

async def send_hourly_report():
    """Send hourly market report"""
    try:
        bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
        chat_id = int(os.getenv("TELEGRAM_CHAT_ID"))
        
        logger.info("📊 Fetching portfolio data...")
        portfolio = get_portfolio_data()
        logger.info(f"Portfolio returned: {portfolio}")
        
        logger.info("📈 Fetching market metrics...")
        metrics = get_market_metrics()
        logger.info(f"Metrics returned: {metrics}")
        
        logger.info("📝 Formatting message...")
        message = format_message(portfolio, metrics)
        logger.info(f"Message length: {len(message)} chars")
        
        logger.info(f"📤 Sending to {chat_id}...")
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        logger.info(f"✅ Sent hourly report to {chat_id}")
        
    except Exception as e:
        logger.error(f"❌ Failed to send report: {e}")
        import traceback
        logger.error(traceback.format_exc())


# ============================================================================
# SCHEDULER
# ============================================================================

async def main():
    """Main scheduler loop"""
    try:
        scheduler = AsyncIOScheduler()
        
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
        await asyncio.Event().wait()
        
    except Exception as e:
        logger.error(f"❌ Scheduler error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
