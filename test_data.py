import os
from dotenv import load_dotenv

load_dotenv()

# Import the functions from market_bot
import sys
sys.path.insert(0, 'C:\\Portfolio_milnai_bot')

# Test yfinance directly
import yfinance as yf

print("Testing yfinance download...")
data = yf.download("RKLB", period="1d", progress=False, timeout=10)
print(f"RKLB Close: {data['Close'].iloc[-1]}")

# Now test portfolio function
from market_bot import get_portfolio_data, get_market_metrics

print("\n=== Testing get_portfolio_data() ===")
portfolio = get_portfolio_data()
print(f"Result: {portfolio}")

print("\n=== Testing get_market_metrics() ===")
metrics = get_market_metrics()
print(f"Result: {metrics}")