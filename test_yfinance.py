import yfinance as yf

print("Testing yfinance...")
try:
    data = yf.download("RKLB", period="1d", progress=False, timeout=10)
    print(f"✅ Downloaded RKLB data")
    print(f"Close price: {data['Close'].iloc[-1]}")
except Exception as e:
    print(f"❌ Error: {e}")