import asyncio
import os
import uuid
import random
import string
import time
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

app = Client("gdrive_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
import utils.logger
utils.logger.bot_instance = app

user_tasks = {}

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
        await clean_invalid_tokens(user_id)  # remove corrupted entries
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
    percent = (current * 100) // total if total else 0
    bar = "█" * (percent // 10) + "░" * (10 - (percent // 10))
    try:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{task_id}")]]) if task_id else None
        await status_msg.edit_text(f"{text}\n{bar} {percent}%", reply_markup=keyboard)
    except:
        pass

async def progress_callback(current, total, status_msg, text, task_id=None):
    await _progress_coro(current, total, status_msg, text, task_id)

def format_storage_bar(used_gb, total_gb):
    percent = (used_gb / total_gb) * 100 if total_gb else 0
    filled = int(percent // 10)
    bar = "🟩" * filled + "⬜" * (10 - filled)
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

# ========== User Commands (part 1) ==========
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
        "I can upload Videos, Documents, Photos, Audio, Voice Notes, GIFs, and more directly from Telegram to your Google Drive.\n\n"
        "2️⃣ **Upload Files from Direct Download Links**\n"
        "Just send me any direct download link (non–IP-restricted), and I’ll upload the file to your Drive. You can even set custom filenames.\n\n"
        "🔐 **Linking Google Drive**\n"
        "1️⃣ Get your Sign In link from /log_in.\n"
        "2️⃣ Open the link → Sign in with your Google Account → Allow access. That’s it! 🤠\n\n"
        "✨ You can link multiple Google Drive accounts using the same method.\n"
        "✨ Switch between accounts using /mygdrives.\n"
        "✨ Log out using /log_out.\n\n"
        "📤 **How to Use the Bot**\n"
        "➤ **Uploading Telegram Files**\nJust forward/send any Telegram file, and I’ll upload it automatically.\n\n"
        "➤ **Uploading From Direct Links**\nSend me any valid direct download URL.\nYou may also attach a custom filename using the format: `download-link | custom filename`.\n\n"
        "🧾 **Other Commands**\n"
        "📊 /stats – View Drive storage stats\n"
        "👤 /account – View your account details\n"
        "⭐ /myplan – View current plan and usage\n"
        "🎁 /referral – Get your referral link\n"
        "💳 /upgrade – Activate premium (via referral rewards)\n"
        "📁 /setfolder – Set default upload folder\n"
        "🗑 /removefolder – Remove upload folder\n"
        "🔒 /privacy – Privacy policy\n"
        "🚪 /log_out – Logout from a Drive account\n"
        "📂 /mydrives – List linked Drive accounts"
    )

@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    text = """**Commands:**
/log_in – Connect a Google Drive account
/log_out – Remove a linked account
/mydrives – List your linked accounts
/upload <url> [filename] – Upload from direct link
/yt <YouTube URL> – Download & upload YouTube video
/setfolder <folder_url> – Default upload folder
/removefolder – Remove custom folder
/myplan – Your plan & usage
/stats – Drive storage stats
/account – Account details
/referral – Get referral link
/upgrade – Activate premium (if rewards available)
/privacy – Privacy policy & ToS"""
    await message.reply(text)

@app.on_message(filters.command("privacy"))
async def privacy_cmd(client, message):
    await message.reply("**Privacy Policy**\n\nYour Google Drive tokens are stored encrypted and used only for file uploads. We do not share your data with third parties.\n\n**Terms of Service**\nThis bot is provided as-is. We are not responsible for misuse of your Drive. Use at your own risk.")

@app.on_message(filters.command("log_in"))
async def login_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    plan = user.get("plan", "free")
    max_accounts = PLANS[plan]["accounts"]
    valid_count = await count_valid_drives(user_id)
    if valid_count >= max_accounts:
        await message.reply(
            f"❌ You already have {max_accounts} valid Google Drive accounts.\n"
            f"Use `/log_out` to remove one, or upgrade your plan.\n"
            f"Your plan: **{plan.upper()}** (max {max_accounts} accounts)."
        )
        return
    domain = DOMAIN.rstrip('/')
    auth_url = f"{domain}/auth/login?user_id={user_id}&action=add"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔐 Authorize Google Drive", url=auth_url)]])
    await message.reply(
        "Open the login page URL, click Sign in with Google, select your Google account, read the privacy policy, and give access.\n\n"
        "**Note:** This is a unique URL, only you can/should use this. If you generate new Sign In URL, this URL will not be valid anymore!\n"
        "If the button does not work, [click here]({})".format(auth_url),
        reply_markup=kb,
        disable_web_page_preview=True
    )

@app.on_message(filters.command("log_out"))
async def logout_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
    if not drives:
        await message.reply("You have no linked Google accounts.")
        return
    if len(drives) == 1:
        email = drives[0].get("email", "Unknown")
        await remove_drive_account(user_id, email)
        await message.reply(f"✅ Successfully logged out from {email}! Use /log_in to login again.")
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
        await message.reply("You have not logged in. Use /help to know how to log in.")
        return
    text = "**Your linked Google Drive accounts:**\n\n"
    for idx, email in enumerate(drives, 1):
        text += f"{idx}. {email}\n"
    await message.reply(text)

@app.on_message(filters.command("stats"))
async def stats_cmd(client, message):
    user_id = message.from_user.id
    drives = await get_user_drives(user_id)
    if not drives:
        await message.reply("You have not logged in. Use /help to know how to log in.")
        return
    email = drives[0]
    if email is None:
        await message.reply("❌ Your Google Drive email is missing. Please use /log_in again.")
        return
    if "invalid" in email.lower() or "re‑login" in email.lower() or "old token" in email.lower():
        await message.reply("❌ Your stored Google Drive token is invalid or expired.\nPlease use /log_in again to re‑authenticate.")
        return
    stats = await get_drive_stats(user_id, email)
    if not stats:
        await message.reply("Failed to fetch Drive stats. Make sure your token is valid.")
        return
    total_gb = stats['total'] / (1024**3)
    used_gb = stats['used'] / (1024**3)
    trash_gb = stats['trash'] / (1024**3)
    free_gb = total_gb - used_gb
    bar, percent = format_storage_bar(used_gb, total_gb)
    await message.reply(
        f"**Display Name:** {message.from_user.first_name}\n"
        f"**Email:** {email}\n\n"
        f"**Total Available Storage:** {total_gb:.2f} GB\n"
        f"**Total Storage Used:** {used_gb:.2f} GB\n"
        f"**Total Storage Used in Trash:** {trash_gb:.2f} GB\n"
        f"**Total Free Storage:** {free_gb:.2f} GB\n\n"
        f"{bar} ({percent:.2f}%) used of {total_gb:.1f} GB."
    )
    
@app.on_message(filters.command("account"))
async def account_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
    email = drives[0].get("email") if drives else "Not connected"
    plan = user.get("plan", "free")
    used = user.get("daily_upload_count", 0)
    limit = PLANS[plan]["daily_uploads"]
    bar, percent = format_storage_bar(used, limit)
    reset_time = get_quota_reset_time(user)

    # Today's uploaded MB
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    pipeline_today = [
        {"$match": {
            "user_id": user_id,
            "action": "upload",
            "status": "success",
            "timestamp": {"$gte": today_start}
        }},
        {"$group": {"_id": None, "total_mb": {"$sum": "$size_mb"}}}
    ]
    result_today = await logs_col.aggregate(pipeline_today).to_list(length=1)
    today_mb = result_today[0]["total_mb"] if result_today else 0

    # Total uploaded MB (all time)
    pipeline_total = [
        {"$match": {"user_id": user_id, "action": "upload", "status": "success"}},
        {"$group": {"_id": None, "total_mb": {"$sum": "$size_mb"}}}
    ]
    result_total = await logs_col.aggregate(pipeline_total).to_list(length=1)
    total_mb = result_total[0]["total_mb"] if result_total else 0
    total_gb = total_mb / 1024

    await message.reply(
        f"**Name:** {message.from_user.first_name}\n"
        f"**Telegram Id:** {user_id}\n"
        f"**Referral:** {user.get('referred_by') and 'Referred by someone' or 'You were not referred by anyone!'}\n\n"
        f"🔗 **Your Referral Link**\n"
        f"`https://t.me/{ (await client.get_me()).username }?start=ref_{user.get('referral_code')}`\n"
        f"Share this link with your friends and earn free plan upgrades! (Read /referral to know more)\n\n"
        f"⭐ **Current Plan:** {plan.capitalize()} User:\n"
        f"    Used: {used} / {limit}\n"
        f"    Balance: {limit - used}\n\n"
        f"    {bar} ({percent:.1f}%)\n\n"
        f"    Your quota will reset in {reset_time}.\n\n"
        f"📊 **Data Usage**\n"
        f"• Today: {today_mb:.2f} MB\n"
        f"• Total (since {user.get('created_at', datetime.utcnow()).strftime('%Y-%m-%d')}): {total_gb:.2f} GB"
    )

@app.on_message(filters.command("referral"))
async def referral_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    code = user.get("referral_code")
    bot_username = (await client.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    remaining = await get_remaining_uploads(user_id)
    plan = user.get("plan", "free")
    limit = PLANS[plan]["daily_uploads"]
    used = user.get("daily_upload_count", 0)
    percent = (used / limit) * 100 if limit else 0
    bar = "⬜" * 10
    if percent > 0:
        filled = int(percent // 10)
        bar = "🟩" * filled + "⬜" * (10 - filled)
    reset_time = get_quota_reset_time(user)
    await message.reply(
        f"**Name:** {message.from_user.first_name}\n"
        f"**Telegram Id:** {user_id}\n"
        f"You were not referred by anyone!\n\n"
        f"Your referral link is:\n`{link}`\n"
        f"Share this link and refer your friends to get free plan upgrade!! (Read /referral to know more)\n\n"
        f"Your current plan is {plan.capitalize()} User:\n"
        f"    Used quota {used} of {limit}, balance {limit - used}.\n\n"
        f"    {bar} ({percent:.1f}%)\n\n"
        f"    Your quota will reset in {reset_time}.\n\n"
        f"Use /my_plans to check your upcoming plans.\n\n"
        f"You have transferred 0 B today and {0} GB in total (data since {user.get('created_at', datetime.utcnow()).strftime('%Y-%m-%d')})."
    )

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
        expiry_date = expiry.strftime("%d-%m-%Y")
        expiry_time = expiry.strftime("%I:%M:%S %p")
        text = (
            f"⚜️ **Premium User Data:**\n\n"
            f"👤 User: {message.from_user.first_name}\n"
            f"⚡ User ID: {user_id}\n"
            f"⏰ Time Left: {days} days, {hours} hours, {minutes} minutes\n"
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

# ========== File Upload Handlers ==========
@app.on_message(filters.document | filters.video | filters.audio | filters.photo | filters.voice)
async def handle_file(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    drives = [d for d in user.get("drive_tokens", []) if isinstance(d, dict)]
    if not drives:
        await message.reply("You have not logged in. Use /help to know how to log in.")
        return
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return
    folder_id = user.get("custom_folder_id")
    await process_file_upload(client, message, user, folder_id)

async def process_file_upload(client, message, user, folder_id):
    user_id = message.from_user.id
    status_msg = await message.reply("⏳ Downloading file...")
    os.makedirs("downloads", exist_ok=True)
    temp_path = f"downloads/{user_id}_{uuid.uuid4()}.tmp"
    try:
        task_id = str(uuid.uuid4())
        safe_progress = make_safe_progress_callback(status_msg, "⏳ Downloading file...", task_id)
        file_path = await client.download_media(
            message,
            file_name=temp_path,
            progress=safe_progress
        )
        user_tasks.setdefault(user_id, {})[task_id] = asyncio.current_task()
        # Get file details
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
        add_to_queue(user_id, file_path, filename, folder_id, status_msg.edit_text)
        await log_action(user_id, "upload", "queued", filename, size_mb)
    except asyncio.CancelledError:
        await status_msg.edit_text("❌ Upload cancelled.")
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed: {str(e)}")
        await log_action(user_id, "upload", "failed", error=str(e))

# ========== Continue from Part 1 ==========
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
        await message.reply("You have not logged in. Use /help to know how to log in.")
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
        task_id = str(uuid.uuid4())
        user_tasks.setdefault(user_id, {})[task_id] = asyncio.current_task()
        await download_http(url, file_path, make_safe_progress_callback(status_msg, "⏳ Downloading from URL...", task_id))
        size_mb = os.path.getsize(file_path) / 1e6
        await status_msg.edit_text("📤 Queuing upload...")
        add_to_queue(user_id, file_path, safe_filename, folder_id, status_msg.edit_text)
        await log_action(user_id, "upload", "queued", safe_filename, size_mb)
    except asyncio.CancelledError:
        await status_msg.edit_text("❌ Upload cancelled.")
        if os.path.exists(file_path):
            os.remove(file_path)
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
        await message.reply("You have not logged in. Use /help to know how to log in.")
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
        task_id = str(uuid.uuid4())
        user_tasks.setdefault(user_id, {})[task_id] = asyncio.current_task()
        final_path = await download_youtube(url, temp_template, make_safe_progress_callback(status_msg, "⏳ Downloading YouTube...", task_id))
        filename = os.path.basename(final_path)
        size_mb = os.path.getsize(final_path) / 1e6
        await status_msg.edit_text("📤 Queuing upload...")
        add_to_queue(user_id, final_path, filename, folder_id, status_msg.edit_text)
        await log_action(user_id, "upload", "queued", filename, size_mb)
    except asyncio.CancelledError:
        await status_msg.edit_text("❌ Upload cancelled.")
    except Exception as e:
        await status_msg.edit_text(f"❌ YouTube download failed: {str(e)}")
        await log_action(user_id, "upload", "failed", error=str(e))

# ========== Callback Queries ==========
@app.on_callback_query()
async def callback_handler(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    if data.startswith("logout_"):
        email = data[7:]
        await remove_drive_account(user_id, email)
        await callback_query.message.edit_text(f"✅ Logged out from {email}.")
    elif data.startswith("cancel_"):
        task_id = data[7:]
        if user_id in user_tasks and task_id in user_tasks[user_id]:
            user_tasks[user_id][task_id].cancel()
            await callback_query.message.edit_text("❌ Operation cancelled.")
    await callback_query.answer()

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
    try:
        expiry_date = new_expiry.strftime("%d-%m-%Y")
        expiry_time = new_expiry.strftime("%I:%M:%S %p")
        join_date = now.strftime("%d-%m-%Y")
        join_time = now.strftime("%I:%M:%S %p")
        await client.send_message(
            target_id,
            f"⚜️ **Premium User Data:**\n\n"
            f"👋 Hey {user.get('first_name', 'User')},\n"
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
