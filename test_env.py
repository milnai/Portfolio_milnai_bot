import os
from dotenv import load_dotenv

load_dotenv()

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

print(f"Bot Token: {bot_token}")
print(f"Chat ID: {chat_id}")

if bot_token and chat_id:
    print("✅ Env loaded correctly!")
else:
    print("❌ Env variables missing!")