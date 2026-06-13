import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "25331263"))
API_HASH = os.getenv("API_HASH", "cab85305bf85125a2ac053210bcd1030")
BOT_TOKEN = os.getenv("BOT_TOKEN", "7977209272:AAEX0GrXV0hjWPJx6E_HLq-uOjAlqd7mul4")
MONGO_URL = os.getenv("MONGO_URL", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", "-1003909289740"))
DOMAIN = os.getenv("DOMAIN", "https://integral-kial-jsssbeniwa-7e4dcaa1.koyeb.app")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://integral-kial-jsssbeniwa-7e4dcaa1.koyeb.app/auth/callback")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# Plan limits (uploads per day, parallel uploads, accounts)
PLANS = {
    "free": {"daily_uploads": 4, "parallel_uploads": 1, "accounts": 1},
    "premium": {"daily_uploads": 50, "parallel_uploads": 2, "accounts": 2},
}
