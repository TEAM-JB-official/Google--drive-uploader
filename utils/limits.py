from datetime import date
from config import PLANS
from db.mongo import users_col

async def check_quota(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return False, "User not found"
    plan = user.get("plan", "free")
    daily_limit = PLANS[plan]["daily_uploads"]
    today = date.today().isoformat()
    last_reset = user.get("last_reset_date")
    if last_reset != today:
        await users_col.update_one(
            {"_id": user_id},
            {"$set": {"daily_upload_count": 0, "last_reset_date": today}}
        )
        user["daily_upload_count"] = 0
    used = user.get("daily_upload_count", 0)
    if used >= daily_limit:
        return False, f"Daily upload limit reached ({daily_limit}). Upgrade to premium for more."
    # Increment usage
    await users_col.update_one({"_id": user_id}, {"$inc": {"daily_upload_count": 1}})
    return True, f"Upload allowed. Used {used+1}/{daily_limit} today."

async def get_remaining_uploads(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return 0
    plan = user.get("plan", "free")
    daily_limit = PLANS[plan]["daily_uploads"]
    today = date.today().isoformat()
    last_reset = user.get("last_reset_date")
    if last_reset != today:
        return daily_limit
    used = user.get("daily_upload_count", 0)
    return max(0, daily_limit - used)
