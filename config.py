import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", ""))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGO_URL = os.getenv("MONGO_URL", "")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", ""))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
DOMAIN = os.getenv("DOMAIN", "")
REDIRECT_URI = os.getenv("REDIRECT_URI", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# Plan limits (uploads per day, parallel uploads, accounts)
PLANS = {
    "free": {"daily_uploads": 4, "parallel_uploads": 1, "accounts": 1},
    "premium": {"daily_uploads": 50, "parallel_uploads": 2, "accounts": 2},
}
