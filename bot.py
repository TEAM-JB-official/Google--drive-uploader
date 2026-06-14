import asyncio
import os
import uuid
import random
import string
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_IDS, DOMAIN, PLANS
from db.mongo import users_col, logs_col
from utils.limits import check_quota, get_remaining_uploads, is_premium_active
from utils.drive import validate_folder
from utils.queue import add_to_queue
from utils.logger import log_action, bot_instance as logger_bot
from utils.downloader import download_http, download_youtube

app = Client("gdrive_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
import utils.logger
utils.logger.bot_instance = app

# ========== Helper Functions ==========
async def get_user(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        referral_code = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        await users_col.insert_one({
            "_id": user_id,
            "plan": "free",
            "premium_expiry": None,
            "daily_upload_count": 0,
            "last_reset_date": None,
            "drive_tokens": None,
            "custom_folder_id": None,
            "referral_code": referral_code,
            "referred_by": None,
            "referral_rewards": 0,
            "created_at": datetime.utcnow()
        })
        user = await users_col.find_one({"_id": user_id})
    return user

async def progress_callback(current, total, status_msg, text):
    percent = (current * 100) // total if total else 0
    bar = "█" * (percent // 10) + "░" * (10 - (percent // 10))
    try:
        await status_msg.edit_text(f"{text}\n{bar} {percent}%")
    except:
        pass

def parse_duration(duration_str):
    """Parse duration like '1 day', '2 days', '1 month', '3 months', '1 year'"""
    duration_str = duration_str.lower().strip()
    parts = duration_str.split()
    if len(parts) != 2:
        return None
    try:
        value = int(parts[0])
    except:
        return None
    unit = parts[1]
    if unit in ['day', 'days']:
        return timedelta(days=value)
    elif unit in ['week', 'weeks']:
        return timedelta(weeks=value)
    elif unit in ['month', 'months']:
        return timedelta(days=value*30)
    elif unit in ['year', 'years']:
        return timedelta(days=value*365)
    else:
        return None

def format_expiry(expiry_dt):
    if not expiry_dt:
        return "No premium"
    now = datetime.utcnow()
    if expiry_dt < now:
        return "Expired"
    delta = expiry_dt - now
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    return f"{days} days, {hours} hours, {minutes} minutes"

# ========== User Commands ==========
@app.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    user_id = message.from_user.id
    args = message.command
    if len(args) > 1 and args[1].startswith("ref_"):
        referrer_code = args[1][4:]
        referrer = await users_col.find_one({"referral_code": referrer_code})
        if referrer and referrer["_id"] != user_id:
            existing = await users_col.find_one({"_id": user_id})
            if not existing or existing.get("referred_by") is None:
                await users_col.update_one({"_id": user_id}, {"$set": {"referred_by": referrer["_id"]}})
                referral_count = await users_col.count_documents({"referred_by": referrer["_id"]})
                if referral_count % 3 == 0 and referral_count > 0:
                    await users_col.update_one({"_id": referrer["_id"]}, {"$inc": {"referral_rewards": 7}})
                    await client.send_message(referrer["_id"], "🎉 You earned 7 days of premium from referrals! Use /myplan to activate.")
    await message.reply(
        "**🚀 Google Drive Bot**\n\n"
        "Send me any file, direct link, or YouTube URL.\n"
        "• /login – Connect Google Drive\n"
        "• /setfolder – Set custom folder\n"
        "• /myplan – Check usage\n"
        "• /referral – Get invite link\n\n"
        "Use /help for all commands."
    )

@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    text = """**Commands:**
/login – Connect Google Drive
/upload <url> [filename] – Upload from direct link
/yt <YouTube URL> – Download & upload YouTube video
/setfolder <folder_url> – Default upload folder
/removefolder – Remove custom folder
/myplan – Your plan & usage
/stats – Upload statistics
/account – Account details
/referral – Get referral link
/upgrade – Activate premium (if rewards available)
**Admin only:**
/add <user_id> <duration> – Add premium (e.g., /add 123456789 1 month)
/rem <user_id> – Remove premium"""
    await message.reply(text)

@app.on_message(filters.command("login"))
async def login_cmd(client, message):
    user_id = message.from_user.id
    await get_user(user_id)
    domain = DOMAIN.rstrip('/')
    auth_url = f"{domain}/auth/login?user_id={user_id}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔐 Authorize Google Drive", url=auth_url)]])
    await message.reply("Click the button to connect your Google Drive:", reply_markup=kb)

@app.on_message(filters.document | filters.video | filters.audio | filters.photo | filters.voice)
async def handle_file(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    # Check if user has Google Drive connected
    if not user.get("drive_tokens"):
        await message.reply("❌ **Google Drive not connected!**\n\nPlease use /login to authenticate your Google Drive first.")
        return
    
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return
    
    folder_id = user.get("custom_folder_id")
    status_msg = await message.reply("⏳ Downloading file...")
    os.makedirs("downloads", exist_ok=True)
    temp_path = f"downloads/{user_id}_{uuid.uuid4()}.tmp"
    
    try:
        file_path = await client.download_media(
            message,
            file_name=temp_path,
            progress=progress_callback,
            progress_args=(status_msg, "⏳ Downloading file...")
        )
        
        # Safely extract filename and size
        if message.document:
            filename = message.document.file_name
            file_size = message.document.file_size
        elif message.video:
            filename = message.video.file_name or f"video_{message.id}.mp4"
            file_size = message.video.file_size
        elif message.audio:
            filename = message.audio.file_name or f"audio_{message.id}.mp3"
            file_size = message.audio.file_size
        elif message.photo:
            filename = f"photo_{message.id}.jpg"
            # Handle both list and single object
            if hasattr(message.photo, '__iter__') and not isinstance(message.photo, str):
                file_size = message.photo[0].file_size if message.photo else 0
            else:
                file_size = getattr(message.photo, 'file_size', 0)
        elif message.voice:
            filename = f"voice_{message.id}.ogg"
            file_size = message.voice.file_size
        else:
            filename = f"file_{message.id}"
            file_size = 0
        
        size_mb = file_size / 1e6
        await status_msg.edit_text("📤 Queuing upload...")
        add_to_queue(user_id, file_path, filename, folder_id, status_msg.edit_text, status_msg.id)
        await log_action(user_id, "upload", "queued", filename, size_mb)
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed: {str(e)}")
        await log_action(user_id, "upload", "failed", error=str(e))

@app.on_message(filters.command("upload"))
async def upload_url_cmd(client, message):
    user_id = message.from_user.id
    args = message.command
    if len(args) < 2:
        await message.reply("Usage: /upload <direct_url> [filename]")
        return
    url = args[1]
    filename = args[2] if len(args) > 2 else url.split('/')[-1].split('?')[0] or "file"
    user = await get_user(user_id)
    
    # Check if user has Google Drive connected
    if not user.get("drive_tokens"):
        await message.reply("❌ **Google Drive not connected!**\n\nPlease use /login to authenticate your Google Drive first.")
        return
    
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return
    folder_id = user.get("custom_folder_id")
    status_msg = await message.reply("⏳ Downloading from URL...")
    os.makedirs("downloads", exist_ok=True)
    safe_filename = "".join(c for c in filename if c.isalnum() or c in '._-')[:100]
    file_path = f"downloads/{user_id}_{uuid.uuid4()}_{safe_filename}"
    try:
        await download_http(url, file_path, progress_callback, status_msg, "⏳ Downloading from URL...")
        size_mb = os.path.getsize(file_path) / 1e6
        await status_msg.edit_text("📤 Queuing upload...")
        add_to_queue(user_id, file_path, safe_filename, folder_id, status_msg.edit_text, status_msg.id)
        await log_action(user_id, "upload", "queued", safe_filename, size_mb)
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed: {str(e)}")
        await log_action(user_id, "upload", "failed", error=str(e))

@app.on_message(filters.command("yt"))
async def youtube_cmd(client, message):
    user_id = message.from_user.id
    args = message.command
    if len(args) < 2:
        await message.reply("Usage: /yt <YouTube URL>")
        return
    url = args[1]
    user = await get_user(user_id)
    
    # Check if user has Google Drive connected
    if not user.get("drive_tokens"):
        await message.reply("❌ **Google Drive not connected!**\n\nPlease use /login to authenticate your Google Drive first.")
        return
    
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return
    folder_id = user.get("custom_folder_id")
    status_msg = await message.reply("⏳ Downloading YouTube video...")
    os.makedirs("downloads", exist_ok=True)
    temp_template = f"downloads/{user_id}_{uuid.uuid4()}_%(title)s.%(ext)s"
    try:
        final_path = await download_youtube(url, temp_template, progress_callback, status_msg)
        filename = os.path.basename(final_path)
        size_mb = os.path.getsize(final_path) / 1e6
        await status_msg.edit_text("📤 Queuing upload...")
        add_to_queue(user_id, final_path, filename, folder_id, status_msg.edit_text, status_msg.id)
        await log_action(user_id, "upload", "queued", filename, size_mb)
    except Exception as e:
        await status_msg.edit_text(f"❌ YouTube download failed: {str(e)}")
        await log_action(user_id, "upload", "failed", error=str(e))

@app.on_message(filters.command("setfolder"))
async def set_folder_cmd(client, message):
    user_id = message.from_user.id
    args = message.command
    if len(args) < 2:
        await message.reply("Usage: /setfolder <google_drive_folder_url>")
        return
    folder_url = args[1]
    folder_id, error = await validate_folder(user_id, folder_url)
    if error:
        await message.reply(f"❌ {error}")
        return
    await users_col.update_one({"_id": user_id}, {"$set": {"custom_folder_id": folder_id}})
    await message.reply("✅ Default upload folder set.")

@app.on_message(filters.command("removefolder"))
async def remove_folder_cmd(client, message):
    user_id = message.from_user.id
    await users_col.update_one({"_id": user_id}, {"$set": {"custom_folder_id": None}})
    await message.reply("✅ Custom folder removed. Uploads go to Drive root.")

@app.on_message(filters.command("stats"))
async def stats_cmd(client, message):
    user_id = message.from_user.id
    cursor = logs_col.aggregate([
        {"$match": {"user_id": user_id, "action": "upload", "status": "success"}},
        {"$count": "count"}
    ])
    result = await cursor.to_list(length=1)
    total_uploads = result[0]["count"] if result else 0
    await message.reply(f"📊 **Your Stats**\nTotal successful uploads: {total_uploads}")

@app.on_message(filters.command("account"))
async def account_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    email = user.get("email", "Not connected")
    plan = user.get("plan", "free")
    used = user.get("daily_upload_count", 0)
    limit = PLANS[plan]["daily_uploads"]
    folder = user.get("custom_folder_id", "Not set")
    text = f"**Account**\nUser ID: {user_id}\nEmail: {email}\nPlan: {plan}\nToday: {used}/{limit}\nFolder: {folder}"
    await message.reply(text)

@app.on_message(filters.command("referral"))
async def referral_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    code = user.get("referral_code")
    bot_username = (await client.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    await message.reply(f"Your referral link:\n{link}\n\nFor every 3 friends who join, you get 7 days of premium!")

@app.on_message(filters.command("myplan"))
async def myplan_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    plan = user.get("plan", "free")
    expiry = user.get("premium_expiry")
    remaining = await get_remaining_uploads(user_id)
    if plan == "premium" and expiry and expiry > datetime.utcnow():
        time_left = format_expiry(expiry)
        expiry_date = expiry.strftime("%d-%m-%Y")
        expiry_time = expiry.strftime("%I:%M:%S %p")
        text = (
            f"⚜️ **Premium User Data:**\n\n"
            f"👤 User: {message.from_user.first_name}\n"
            f"⚡ User ID: {user_id}\n"
            f"⏰ Time Left: {time_left}\n"
            f"⌛️ Expiry Date: {expiry_date}\n"
            f"⏱️ Expiry Time: {expiry_time}\n\n"
            f"📤 Daily Uploads Left: {remaining}"
        )
    else:
        text = f"**Your Plan:** {plan.upper()}\n**Daily Uploads Left:** {remaining}"
        if user.get("referral_rewards", 0) > 0:
            text += "\n\nType /upgrade to activate premium days."
    await message.reply(text)

@app.on_message(filters.command("upgrade"))
async def upgrade_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    rewards = user.get("referral_rewards", 0)
    if rewards > 0:
        new_expiry = datetime.utcnow() + timedelta(days=rewards)
        await users_col.update_one(
            {"_id": user_id},
            {"$set": {"plan": "premium", "premium_expiry": new_expiry, "referral_rewards": 0}}
        )
        await message.reply(f"🎉 Upgraded to Premium for {rewards} days! Enjoy 50 daily uploads.")
    else:
        await message.reply("No reward days available. Invite friends using /referral to earn premium.")

# ========== Admin Commands ==========
@app.on_message(filters.command("add") & filters.user(ADMIN_IDS))
async def add_premium_cmd(client, message):
    args = message.text.split()
    if len(args) < 3:
        await message.reply("Usage: /add <user_id> <duration>\nExample: /add 123456789 1 month")
        return
    try:
        target_id = int(args[1])
    except:
        await message.reply("❌ Invalid user ID.")
        return
    duration_str = " ".join(args[2:])
    delta = parse_duration(duration_str)
    if not delta:
        await message.reply("❌ Invalid duration. Use: '1 day', '2 weeks', '1 month', '1 year'")
        return
    user = await get_user(target_id)
    now = datetime.utcnow()
    new_expiry = now + delta
    await users_col.update_one(
        {"_id": target_id},
        {"$set": {"premium_expiry": new_expiry, "plan": "premium"}}
    )
    # Send notification to user
    try:
        expiry_date = new_expiry.strftime("%d-%m-%Y")
        expiry_time = new_expiry.strftime("%I:%M:%S %p")
        join_date = now.strftime("%d-%m-%Y")
        join_time = now.strftime("%I:%M:%S %p")
        await client.send_message(
            target_id,
            f"⚜️ **Premium User Data:**\n\n"
            f"👋 Hey {message.from_user.first_name},\n"
            f"Thank you for purchasing premium.\nEnjoy!! ✨🎉\n\n"
            f"⏰ **Premium Access:** {duration_str}\n"
            f"⏳ **Joining Date:** {join_date}\n"
            f"⏱️ **Joining Time:** {join_time}\n\n"
            f"⌛️ **Expiry Date:** {expiry_date}\n"
            f"⏱️ **Expiry Time:** {expiry_time}\n\n"
            f"**Daily Uploads Left:** 50 (Premium)"
        )
    except:
        pass
    await message.reply(
        f"✅ Premium added successfully!\n\n"
        f"👤 User ID: {target_id}\n"
        f"⏰ Premium Access: {duration_str}\n"
        f"⌛️ Expiry: {new_expiry.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await log_action(target_id, "admin_add_premium", "success", filename=f"duration:{duration_str}")

@app.on_message(filters.command("rem") & filters.user(ADMIN_IDS))
async def remove_premium_cmd(client, message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("Usage: /rem <user_id>")
        return
    try:
        target_id = int(args[1])
    except:
        await message.reply("❌ Invalid user ID.")
        return
    await users_col.update_one(
        {"_id": target_id},
        {"$set": {"premium_expiry": None, "plan": "free"}}
    )
    await message.reply(f"✅ Premium removed for user {target_id}.")
    await log_action(target_id, "admin_remove_premium", "success")

@app.on_message(filters.command("broadcast") & filters.user(ADMIN_IDS))
async def broadcast_cmd(client, message):
    if len(message.command) < 2:
        await message.reply("Usage: /broadcast <message>")
        return
    text = message.text.split(maxsplit=1)[1]
    count = 0
    async for user in users_col.find({}, {"_id": 1}):
        try:
            await client.send_message(user["_id"], text)
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.reply(f"Broadcast sent to {count} users.")

if __name__ == "__main__":
    app.run()
