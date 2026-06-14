import asyncio
import os
import uuid
import random
import string
import time
import re
import hashlib
import aiohttp
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_IDS, DOMAIN, PLANS
from db.mongo import users_col, logs_col
from utils.limits import check_quota, get_remaining_uploads
from utils.drive import (
    get_drive_service, get_drive_stats, add_drive_account, remove_drive_account,
    get_user_drives, upload_file_to_drive, validate_folder, clean_invalid_tokens, count_valid_drives
)
from utils.queue import add_to_queue, cancel_user_task
from utils.logger import log_action, bot_instance as logger_bot
from utils.downloader import download_http, download_youtube
from googleapiclient.http import MediaIoBaseDownload

app = Client("gdrive_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
import utils.logger
utils.logger.bot_instance = app

user_tasks = {}
_download_pending = {}

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
            "drive_tokens": [],
            "active_drive": None,
            "custom_folder_id": None,
            "referral_code": referral_code,
            "referred_by": None,
            "referral_rewards": 0,
            "created_at": datetime.utcnow()
        })
        user = await users_col.find_one({"_id": user_id})
    else:
        await clean_invalid_tokens(user_id)
        user = await users_col.find_one({"_id": user_id})
    return user

def make_safe_progress_callback(status_msg, text, task_id):
    loop = asyncio.get_event_loop()
    def progress_sync(current, total):
        asyncio.run_coroutine_threadsafe(
            _progress_coro(current, total, status_msg, text, task_id),
            loop
        )
    return progress_sync

async def _progress_coro(current, total, status_msg, text, task_id):
    if total <= 0:
        return
    percent = (current * 100) // total
    bar = "█" * (percent // 5) + "░" * (20 - (percent // 5))
    try:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{task_id}")]]) if task_id else None
        await status_msg.edit_text(f"{text}\n[{bar}] {percent}%", reply_markup=keyboard)
    except:
        pass

async def progress_callback(current, total, status_msg, text, task_id=None):
    await _progress_coro(current, total, status_msg, text, task_id)

def format_storage_bar(used_gb, total_gb):
    percent = (used_gb / total_gb) * 100 if total_gb else 0
    filled = int(percent // 5)
    bar = "🟩" * filled + "⬜" * (20 - filled)
    return bar, percent

def get_quota_reset_time(user):
    last_reset = user.get("last_reset_date")
    if not last_reset:
        return "calculating..."
    today = datetime.utcnow().date().isoformat()
    if last_reset == today:
        reset_dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        reset_dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    delta = reset_dt - datetime.utcnow()
    return str(timedelta(seconds=delta.total_seconds())).split('.')[0]

# ========== User Commands ==========
@app.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
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
        f"**Hey {first_name}, Nice to meet you.**\n\n"
        "I'm Google Drive Uploader Bot. I can help you upload files (Telegram files as well as URLs) to your Google Drive, if you authorise me.\n\n"
        "Use /help to know how I work and to authorise me.\n\n"
        "**🤖 What I Can Do**\n"
        "1️⃣ **Upload Telegram Files to Google Drive**\n"
        "2️⃣ **Upload Files from Direct Download Links**\n\n"
        "🔐 **Linking Google Drive**\n1️⃣ /log_in\n2️⃣ Open link → Sign in\n\n"
        "📤 **Uploading**\nJust send a file or use /upload\n\n"
        "🧾 **Commands:** /help"
    )

@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    text = """**Commands:**
/log_in – Connect Google Drive
/log_out – Remove account
/mydrives – List linked accounts
/setdrive – Choose active drive
/showdrive – Show active drive
/upload <url> – Upload from URL
/yt <url> – YouTube upload
/setfolder – Set default folder
/removefolder – Remove folder
/myplan – Plan & usage
/stats – Drive storage stats
/account – Account details
/referral – Referral link
/upgrade – Activate premium
/getdrive <link> – Download file from Drive to Telegram
/privacy – Privacy policy"""
    await message.reply(text)

@app.on_message(filters.command("privacy"))
async def privacy_cmd(client, message):
    await message.reply("**Privacy Policy**\n\nYour tokens are stored encrypted and used only for file operations.")

@app.on_message(filters.command("log_in"))
async def login_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    plan = user.get("plan", "free")
    max_accounts = PLANS[plan]["accounts"]
    valid_count = await count_valid_drives(user_id)
    if valid_count >= max_accounts:
        await message.reply(f"❌ You already have {max_accounts} valid accounts. Use /log_out to remove one.")
        return
    domain = DOMAIN.rstrip('/')
    auth_url = f"{domain}/auth/login?user_id={user_id}&action=add"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔐 Authorize Google Drive", url=auth_url)]])
    await message.reply("Open the login page URL, click Sign in with Google, select your account, and give access.\n\n"
                        "**Note:** Unique URL, only you can use it.\n"
                        f"If button doesn't work: {auth_url}",
                        reply_markup=kb, disable_web_page_preview=True)

@app.on_message(filters.command("log_out"))
async def logout_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
    if not drives:
        await message.reply("No linked accounts.")
        return
    if len(drives) == 1:
        email = drives[0].get("email", "Unknown")
        await remove_drive_account(user_id, email)
        await message.reply(f"✅ Logged out from {email}.")
    else:
        keyboard = []
        for acc in drives:
            email = acc.get("email", "Unknown")
            keyboard.append([InlineKeyboardButton(email, callback_data=f"logout_{email}")])
        await message.reply("Select which account to logout:", reply_markup=InlineKeyboardMarkup(keyboard))

@app.on_message(filters.command("mydrives"))
async def mydrives_cmd(client, message):
    user_id = message.from_user.id
    drives = await get_user_drives(user_id)
    if not drives:
        await message.reply("You have not logged in. Use /log_in.")
        return
    text = "**Linked Google Drive accounts:**\n\n"
    for idx, email in enumerate(drives, 1):
        text += f"{idx}. {email}\n"
    await message.reply(text)

@app.on_message(filters.command("setdrive"))
async def set_drive_cmd(client, message):
    user_id = message.from_user.id
    drives = await get_user_drives(user_id)
    valid_drives = [d for d in drives if "invalid" not in d.lower() and "re‑login" not in d.lower()]
    if not valid_drives:
        await message.reply("No valid linked accounts. Use /log_in first.")
        return
    if len(valid_drives) == 1:
        await users_col.update_one({"_id": user_id}, {"$set": {"active_drive": valid_drives[0]}})
        await message.reply(f"✅ Default drive set to {valid_drives[0]}")
        return
    keyboard = [[InlineKeyboardButton(email, callback_data=f"setdrive_{email}")] for email in valid_drives]
    await message.reply("Select which Drive to use for uploads:", reply_markup=InlineKeyboardMarkup(keyboard))

@app.on_message(filters.command("showdrive"))
async def show_drive_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    active = user.get("active_drive")
    if not active:
        await message.reply("No default drive set. Use /setdrive to choose one.")
    else:
        await message.reply(f"✅ Current default drive: {active}")

async def show_drive_stats(client, message, user_id, email):
    stats = await get_drive_stats(user_id, email)
    if not stats:
        await message.reply(f"Failed to fetch stats for {email}. Make sure your token is valid.")
        return
    total_gb = stats['total'] / (1024**3)
    used_gb = stats['used'] / (1024**3)
    trash_gb = stats['trash'] / (1024**3)
    free_gb = total_gb - used_gb
    bar, percent = format_storage_bar(used_gb, total_gb)
    await message.reply(
        f"**Display Name:** {message.chat.first_name if hasattr(message.chat, 'first_name') else message.from_user.first_name}\n"
        f"**Email:** {email}\n\n"
        f"**Total Available:** {total_gb:.2f} GB\n"
        f"**Used:** {used_gb:.2f} GB\n"
        f"**Trash:** {trash_gb:.2f} GB\n"
        f"**Free:** {free_gb:.2f} GB\n\n"
        f"{bar} ({percent:.2f}%) used of {total_gb:.1f} GB."
    )

@app.on_message(filters.command("stats"))
async def stats_cmd(client, message):
    user_id = message.from_user.id
    drives = await get_user_drives(user_id)
    if not drives:
        await message.reply("You have not logged in. Use /log_in.")
        return
    valid_drives = [d for d in drives if "invalid" not in d.lower() and "re‑login" not in d.lower()]
    if not valid_drives:
        await message.reply("No valid drives found. Please /log_in again.")
        return
    if len(valid_drives) == 1:
        await show_drive_stats(client, message, user_id, valid_drives[0])
        return
    keyboard = [[InlineKeyboardButton(email, callback_data=f"stats_{email}")] for email in valid_drives]
    await message.reply("Select which Google Drive account to view stats for:", reply_markup=InlineKeyboardMarkup(keyboard))

@app.on_message(filters.command("account"))
async def account_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
    if not drives:
        await message.reply("You have not logged in. Use /log_in.")
        return
    active_email = user.get("active_drive") or drives[0].get("email")
    plan = user.get("plan", "free")
    used = user.get("daily_upload_count", 0)
    limit = PLANS[plan]["daily_uploads"]
    bar, percent = format_storage_bar(used, limit)
    reset_time = get_quota_reset_time(user)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    pipeline_today = [{"$match": {"user_id": user_id, "action": "upload", "status": "success", "timestamp": {"$gte": today_start}}},
                      {"$group": {"_id": None, "total_mb": {"$sum": "$size_mb"}}}]
    result_today = await logs_col.aggregate(pipeline_today).to_list(length=1)
    today_mb = result_today[0]["total_mb"] if result_today else 0
    pipeline_total = [{"$match": {"user_id": user_id, "action": "upload", "status": "success"}},
                      {"$group": {"_id": None, "total_mb": {"$sum": "$size_mb"}}}]
    result_total = await logs_col.aggregate(pipeline_total).to_list(length=1)
    total_mb = result_total[0]["total_mb"] if result_total else 0
    total_gb = total_mb / 1024
    await message.reply(
        f"**Name:** {message.from_user.first_name}\n"
        f"**Telegram Id:** {user_id}\n"
        f"**Active Drive Email:** {active_email}\n\n"
        f"**Referral:** {'Referred by someone' if user.get('referred_by') else 'None'}\n\n"
        f"🔗 **Your Referral Link**\n"
        f"`https://t.me/{ (await client.get_me()).username }?start=ref_{user.get('referral_code')}`\n\n"
        f"⭐ **Current Plan:** {plan.capitalize()} User:\n"
        f"    Used: {used} / {limit}\n"
        f"    Balance: {limit - used}\n"
        f"    {bar} ({percent:.1f}%)\n"
        f"    Resets in {reset_time}\n\n"
        f"📊 **Data Usage**\n"
        f"• Today: {today_mb:.2f} MB\n"
        f"• Total (all time): {total_gb:.2f} GB\n\n"
        f"Use `/stats` for Drive storage usage and `/setdrive` to switch accounts."
    )

@app.on_message(filters.command("referral"))
async def referral_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    code = user.get("referral_code")
    bot_username = (await client.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    await message.reply(f"**Your referral link:**\n`{link}`\n\nFor every 3 friends you get 7 days premium.")

@app.on_message(filters.command("myplan"))
async def myplan_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    plan = user.get("plan", "free")
    expiry = user.get("premium_expiry")
    remaining = await get_remaining_uploads(user_id)
    if plan == "premium" and expiry and expiry > datetime.utcnow():
        time_left = expiry - datetime.utcnow()
        days = time_left.days
        hours = time_left.seconds // 3600
        minutes = (time_left.seconds % 3600) // 60
        text = (f"⚜️ **Premium**\n👤 {message.from_user.first_name}\n"
                f"⏰ Time Left: {days}d {hours}h {minutes}m\n"
                f"📤 Daily Uploads Left: {remaining}")
    else:
        text = f"**Plan:** {plan.upper()}\n**Daily Uploads Left:** {remaining}"
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
        await users_col.update_one({"_id": user_id}, {"$set": {"plan": "premium", "premium_expiry": new_expiry, "referral_rewards": 0}})
        await message.reply(f"🎉 Upgraded to Premium for {rewards} days!")
    else:
        await message.reply("No reward days available. Invite friends with /referral.")

@app.on_message(filters.command("setfolder"))
async def set_folder_cmd(client, message):
    user_id = message.from_user.id
    args = message.command
    if len(args) < 2:
        await message.reply("Usage: /setfolder <folder_url>")
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

# ========== File Upload Handlers ==========
async def process_file_upload(client, message, user, folder_id):
    user_id = message.from_user.id
    status_msg = await message.reply("⏳ Downloading file...")
    os.makedirs("downloads", exist_ok=True)
    temp_path = f"downloads/{user_id}_{uuid.uuid4()}.tmp"
    try:
        task_id = str(uuid.uuid4())
        safe_progress = make_safe_progress_callback(status_msg, "⏳ Downloading file...", task_id)
        file_path = await client.download_media(message, file_name=temp_path, progress=safe_progress)
        user_tasks.setdefault(user_id, {})[task_id] = asyncio.current_task()
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
            file_size = message.photo[0].file_size if message.photo else 0
        elif message.voice:
            filename = f"voice_{message.id}.ogg"
            file_size = message.voice.file_size
        else:
            filename = f"file_{message.id}"
            file_size = 0
        size_mb = file_size / 1e6
        await status_msg.edit_text("📤 Queuing upload...")
        active_email = user.get("active_drive")
        add_to_queue(user_id, file_path, filename, folder_id, status_msg.edit_text, email=active_email)
        await log_action(user_id, "upload", "queued", filename, size_mb)
    except asyncio.CancelledError:
        await status_msg.edit_text("❌ Upload cancelled.")
        if os.path.exists(temp_path): os.remove(temp_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed: {str(e)}")
        await log_action(user_id, "upload", "failed", error=str(e))

@app.on_message(filters.document | filters.video | filters.audio | filters.photo | filters.voice)
async def handle_file(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
    if not drives:
        await message.reply("Not logged in. Use /log_in.")
        return
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return

    # ---------- Forward the file to the log channel ----------
    from config import LOG_CHANNEL
    if LOG_CHANNEL:
        try:
            await message.copy(LOG_CHANNEL)
        except Exception as e:
            print(f"Log forward failed: {e}")
    # ---------------------------------------------------------

    folder_id = user.get("custom_folder_id")
    await process_file_upload(client, message, user, folder_id)

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
    drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
    if not drives:
        await message.reply("Not logged in. Use /log_in first.")
        return
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return
    folder_id = user.get("custom_folder_id")
    status_msg = await message.reply("⏳ Downloading from URL...")
    os.makedirs("downloads", exist_ok=True)
    # Short filename to avoid path length issues
    name, ext = os.path.splitext(filename)
    short_name = f"{uuid.uuid4().hex[:8]}{ext}"
    file_path = f"downloads/{user_id}_{short_name}"
    try:
        task_id = str(uuid.uuid4())
        user_tasks.setdefault(user_id, {})[task_id] = asyncio.current_task()
        await download_http(url, file_path, make_safe_progress_callback(status_msg, "⏳ Downloading from URL...", task_id))
        size_mb = os.path.getsize(file_path) / 1e6
        await status_msg.edit_text("📤 Queuing upload...")
        active_email = user.get("active_drive")
        add_to_queue(user_id, file_path, filename, folder_id, status_msg.edit_text, email=active_email)
        await log_action(user_id, "upload", "queued", filename, size_mb)
    except asyncio.CancelledError:
        await status_msg.edit_text("❌ Cancelled.")
        if os.path.exists(file_path): os.remove(file_path)
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
    drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
    if not drives:
        await message.reply("Not logged in. Use /log_in first.")
        return
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return
    folder_id = user.get("custom_folder_id")
    status_msg = await message.reply("⏳ Downloading YouTube...")
    os.makedirs("downloads", exist_ok=True)
    temp_template = f"downloads/{user_id}_{uuid.uuid4()}_%(title)s.%(ext)s"
    try:
        task_id = str(uuid.uuid4())
        user_tasks.setdefault(user_id, {})[task_id] = asyncio.current_task()
        final_path = await download_youtube(url, temp_template, make_safe_progress_callback(status_msg, "⏳ Downloading YouTube...", task_id))
        filename = os.path.basename(final_path)
        size_mb = os.path.getsize(final_path) / 1e6
        await status_msg.edit_text("📤 Queuing upload...")
        active_email = user.get("active_drive")
        add_to_queue(user_id, final_path, filename, folder_id, status_msg.edit_text, email=active_email)
        await log_action(user_id, "upload", "queued", filename, size_mb)
    except asyncio.CancelledError:
        await status_msg.edit_text("❌ Cancelled.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed: {str(e)}")
        await log_action(user_id, "upload", "failed", error=str(e))

# ========== Drive Download (getdrive) ==========
async def _drive_download_progress(current, total, status_msg, task_id):
    if total <= 0:
        return
    percent = (current * 100) // total
    bar = "█" * (percent // 5) + "░" * (20 - (percent // 5))
    current_mb = current / (1024*1024)
    total_mb = total / (1024*1024)
    try:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_dl_{task_id}")]])
        await status_msg.edit_text(f"⏳ Downloading from Drive...\n[{bar}] {percent}%\n\n➡️ {current_mb:.1f} MB of {total_mb:.1f} MB", reply_markup=keyboard)
    except:
        pass

async def download_drive_file(client, message, service, file_id, original_filename, file_size, status_msg):
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    os.makedirs("downloads", exist_ok=True)
    ext = os.path.splitext(original_filename)[1] or ".bin"
    random_name = f"{uuid.uuid4().hex[:8]}{ext}"
    temp_path = f"downloads/{user_id}_{random_name}"
    try:
        task_id = str(uuid.uuid4())
        def progress_sync(current, total):
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(_drive_download_progress(current, total, status_msg, task_id), loop)
        async def download():
            request = service.files().get_media(fileId=file_id)
            with open(temp_path, "wb") as f:
                downloader = MediaIoBaseDownload(f, request, chunksize=1024*1024*5)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    if status:
                        progress_sync(status.resumable_progress, status.total_size)
        await asyncio.to_thread(download)
        await status_msg.edit_text("📤 Sending file to Telegram...")
        await client.send_document(
            chat_id=user_id,
            document=temp_path,
            caption=f"✅ **Downloaded from Drive:**\n`{original_filename}`\n📏 Size: {os.path.getsize(temp_path)/1e6:.2f} MB"
        )
        await status_msg.delete()
        os.remove(temp_path)
    except asyncio.CancelledError:
        await status_msg.edit_text("❌ Download cancelled.")
        if os.path.exists(temp_path): os.remove(temp_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed: {str(e)}")
        if os.path.exists(temp_path): os.remove(temp_path)

async def download_public_drive_file(client, message, file_id, status_msg):
    """Download a public Google Drive file using direct download (no API)."""
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    os.makedirs("downloads", exist_ok=True)

    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(download_url, allow_redirects=True) as resp:
            text = await resp.text()
            # Look for confirm token (Google's virus scan warning)
            match = re.search(r'confirm=([^&"\']+)', text)
            if match:
                confirm = match.group(1)
                download_url = f"https://drive.google.com/uc?export=download&confirm={confirm}&id={file_id}"
                async with session.get(download_url, allow_redirects=True) as resp2:
                    content_disp = resp2.headers.get('Content-Disposition', '')
                    if 'filename=' in content_disp:
                        filename = content_disp.split('filename=')[1].strip('"')
                    else:
                        filename = f"file_{file_id}.bin"
                    total = int(resp2.headers.get('content-length', 0))
                    await status_msg.edit_text(f"⏳ Downloading: {filename}")
                    ext = os.path.splitext(filename)[1] or ".bin"
                    temp_name = f"{uuid.uuid4().hex[:8]}{ext}"
                    temp_path = f"downloads/{user_id}_{temp_name}"
                    downloaded = 0
                    with open(temp_path, 'wb') as f:
                        async for chunk in resp2.content.iter_chunked(1024*1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                percent = (downloaded * 100) // total
                                await status_msg.edit_text(f"⏳ Downloading: {filename}\n{percent}%")
                    await status_msg.edit_text("📤 Sending to Telegram...")
                    await client.send_document(
                        chat_id=user_id,
                        document=temp_path,
                        caption=f"✅ **Downloaded from Public Drive:**\n`{filename}`\n📏 Size: {os.path.getsize(temp_path)/1e6:.2f} MB"
                    )
                    await status_msg.delete()
                    os.remove(temp_path)
            else:
                # No confirmation needed
                content_disp = resp.headers.get('Content-Disposition', '')
                if 'filename=' in content_disp:
                    filename = content_disp.split('filename=')[1].strip('"')
                else:
                    filename = f"file_{file_id}.bin"
                total = int(resp.headers.get('content-length', 0))
                await status_msg.edit_text(f"⏳ Downloading: {filename}")
                ext = os.path.splitext(filename)[1] or ".bin"
                temp_name = f"{uuid.uuid4().hex[:8]}{ext}"
                temp_path = f"downloads/{user_id}_{temp_name}"
                downloaded = 0
                with open(temp_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(1024*1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            percent = (downloaded * 100) // total
                            await status_msg.edit_text(f"⏳ Downloading: {filename}\n{percent}%")
                await status_msg.edit_text("📤 Sending to Telegram...")
                await client.send_document(
                    chat_id=user_id,
                    document=temp_path,
                    caption=f"✅ **Downloaded from Public Drive:**\n`{filename}`\n📏 Size: {os.path.getsize(temp_path)/1e6:.2f} MB"
                )
                await status_msg.delete()
                os.remove(temp_path)

@app.on_message(filters.command("getdrive"))
async def getdrive_cmd(client, message):
    user_id = message.from_user.id
    args = message.command
    if len(args) < 2:
        await message.reply("Usage: /getdrive <google_drive_link>\nExample: /getdrive https://drive.google.com/file/d/abc123/view")
        return
    drive_link = args[1]
    patterns = [r"/file/d/([a-zA-Z0-9_-]+)", r"id=([a-zA-Z0-9_-]+)", r"open\?id=([a-zA-Z0-9_-]+)", r"uc\?id=([a-zA-Z0-9_-]+)"]
    file_id = None
    for p in patterns:
        m = re.search(p, drive_link)
        if m:
            file_id = m.group(1)
            break
    if not file_id:
        await message.reply("❌ Could not extract file ID from the link.")
        return

    status_msg = await message.reply("⏳ Attempting public download...")
    # First try public download (works for any publicly shared file)
    try:
        await download_public_drive_file(client, message, file_id, status_msg)
    except Exception as e:
        # If public fails, try authenticated Drive API
        await status_msg.edit_text("⚠️ Public download failed, trying with your Drive account...")
        user = await get_user(user_id)
        drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
        if not drives:
            await status_msg.edit_text("❌ Not logged in. Use /log_in first.")
            return
        active_email = user.get("active_drive") or drives[0].get("email")
        try:
            service = await get_drive_service(user_id, active_email)
            if not service:
                await status_msg.edit_text("❌ Drive authentication failed. Re‑login with /log_in.")
                return
            await status_msg.edit_text("⏳ Fetching file info from your Drive...")
            file_meta = await asyncio.to_thread(lambda: service.files().get(fileId=file_id, fields="name, size, mimeType").execute())
            original_filename = file_meta.get("name", "file.bin")
            file_size = int(file_meta.get("size", 0))
            size_mb = file_size / 1e6
            mime_type = file_meta.get("mimeType", "")
            if mime_type == "application/vnd.google-apps.folder":
                await status_msg.edit_text("❌ Folders are not supported.")
                return
            display_name = original_filename[:40] + ("..." if len(original_filename) > 40 else "")
            if file_size > 50 * 1024 * 1024:
                token = str(uuid.uuid4())[:8]
                _download_pending[token] = {
                    "file_id": file_id,
                    "filename": original_filename,
                    "file_size": file_size,
                    "user_id": user_id,
                    "active_email": active_email
                }
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes", callback_data=f"confirm_dl_{token}"), InlineKeyboardButton("❌ No", callback_data="cancel_dl")]])
                await status_msg.edit_text(f"⚠️ File: {display_name}\n📏 Size: {size_mb:.2f} MB\n\nProceed?", reply_markup=kb)
                return
            await download_drive_file(client, message, service, file_id, original_filename, file_size, status_msg)
        except Exception as e2:
            await status_msg.edit_text(f"❌ Failed to download from your Drive: {str(e2)}\n\nMake sure the file is either public or shared with your account.")

# ========== Callback Queries ==========
@app.on_callback_query()
async def callback_handler(client, callback_query: CallbackQuery):
    global _download_pending
    data = callback_query.data
    user_id = callback_query.from_user.id

    if data.startswith("logout_"):
        email = data[7:]
        await remove_drive_account(user_id, email)
        await callback_query.message.edit_text(f"✅ Logged out from {email}.")

    elif data.startswith("setdrive_"):
        email = data[9:]
        await users_col.update_one({"_id": user_id}, {"$set": {"active_drive": email}})
        new_text = f"✅ Default drive set to **{email}**.\nNow all uploads will go to this account."
        if callback_query.message.text != new_text:
            await callback_query.message.edit_text(new_text)
        else:
            await callback_query.answer("Drive already set to this account.")

    elif data.startswith("stats_"):
        email = data[6:]
        await show_drive_stats(client, callback_query.message, user_id, email)
        await callback_query.answer()

    elif data.startswith("cancel_"):
        task_id = data[7:]
        if user_id in user_tasks and task_id in user_tasks[user_id]:
            user_tasks[user_id][task_id].cancel()
            await callback_query.message.edit_text("❌ Operation cancelled.")

    elif data.startswith("confirm_dl_"):
        token = data[11:]
        if token in _download_pending:
            info = _download_pending.pop(token)
            if info.get("user_id") != user_id:
                await callback_query.message.edit_text("❌ This confirmation is not for you.")
                await callback_query.answer()
                return
            user = await get_user(user_id)
            active_email = info.get("active_email")
            service = await get_drive_service(user_id, active_email)
            if not service:
                await callback_query.message.edit_text("Authentication failed. Please /log_in again.")
                await callback_query.answer()
                return
            await callback_query.message.edit_text("⏳ Starting download...")
            await download_drive_file(
                client,
                callback_query.message,
                service,
                info["file_id"],
                info["filename"],
                info["file_size"],
                callback_query.message
            )
        else:
            await callback_query.message.edit_text("Invalid or expired confirmation. Please run /getdrive again.")
        await callback_query.answer()

    elif data == "cancel_dl":
        await callback_query.message.edit_text("❌ Download cancelled.")
        await callback_query.answer()

    elif data.startswith("cancel_dl_"):
        task_id = data[9:]
        if user_id in user_tasks and task_id in user_tasks[user_id]:
            user_tasks[user_id][task_id].cancel()
            await callback_query.message.edit_text("❌ Download cancelled.")

    else:
        await callback_query.answer("Unknown action")

# ========== Admin Commands ==========
def parse_duration(duration_str):
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

@app.on_message(filters.command("add") & filters.user(ADMIN_IDS))
async def add_premium_cmd(client, message):
    args = message.text.split()
    if len(args) < 3:
        await message.reply("Usage: /add <user_id> <duration> (e.g., 1 month)")
        return
    try:
        target_id = int(args[1])
    except:
        await message.reply("Invalid user ID.")
        return
    duration_str = " ".join(args[2:])
    delta = parse_duration(duration_str)
    if not delta:
        await message.reply("Invalid duration. Use: '1 day', '1 month', etc.")
        return
    user = await get_user(target_id)
    now = datetime.utcnow()
    new_expiry = now + delta
    await users_col.update_one({"_id": target_id}, {"$set": {"premium_expiry": new_expiry, "plan": "premium"}})
    try:
        await client.send_message(target_id, f"⚜️ **Premium Added!**\nYou now have premium for {duration_str}.\nEnjoy 50 daily uploads.")
    except:
        pass
    await message.reply(f"✅ Premium added to {target_id} for {duration_str}.")
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
        await message.reply("Invalid user ID.")
        return
    await users_col.update_one({"_id": target_id}, {"$set": {"premium_expiry": None, "plan": "free"}})
    await message.reply(f"✅ Premium removed for {target_id}.")
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
