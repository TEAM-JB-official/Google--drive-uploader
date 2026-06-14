from datetime import datetime, date
from config import PLANS
from db.mongo import users_col

async def is_premium_active(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return False
    expiry = user.get("premium_expiry")
    if expiry and expiry > datetime.utcnow():
        return True
    # If expired but plan is premium, downgrade
    if user.get("plan") == "premium" and (not expiry or expiry <= datetime.utcnow()):
        await users_col.update_one({"_id": user_id}, {"$set": {"plan": "free", "premium_expiry": None}})
        return False
    return user.get("plan") == "premium"

async def check_quota(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return False, "User not found"
    # Check premium expiry
    premium_active = await is_premium_active(user_id)
    plan = "premium" if premium_active else "free"
    # Ensure plan field is updated
    if user.get("plan") != plan:
        await users_col.update_one({"_id": user_id}, {"$set": {"plan": plan}})
        user["plan"] = plan
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
        return False, f"Daily upload limit reached ({daily_limit}). Premium users get 50."
    await users_col.update_one({"_id": user_id}, {"$inc": {"daily_upload_count": 1}})
    return True, f"Upload allowed. Used {used+1}/{daily_limit} today."

async def get_remaining_uploads(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return 0
    premium_active = await is_premium_active(user_id)
    plan = "premium" if premium_active else "free"
    daily_limit = PLANS[plan]["daily_uploads"]
    today = date.today().isoformat()
    last_reset = user.get("last_reset_date")
    if last_reset != today:
        return daily_limit
    used = user.get("daily_upload_count", 0)
    return max(0, daily_limit - used)
