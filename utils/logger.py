from datetime import datetime
from db.mongo import logs_col
from config import LOG_CHANNEL

bot_instance = None  # Will be set by bot.py

async def log_action(user_id, action, status, filename="", size_mb=0, error=""):
    doc = {
        "user_id": user_id,
        "action": action,
        "status": status,
        "filename": filename,
        "size_mb": size_mb,
        "error": error,
        "timestamp": datetime.utcnow()
    }
    await logs_col.insert_one(doc)

    # Send to Telegram log channel if available
    if bot_instance and LOG_CHANNEL:
        text = f"👤 User: {user_id}\n📁 File: {filename}\n📏 Size: {size_mb:.2f} MB\n🔄 Action: {action}\n✅ Status: {status}"
        if error:
            text += f"\n⚠️ Error: {error}"
        try:
            await bot_instance.send_message(LOG_CHANNEL, text)
        except Exception:
            pass
