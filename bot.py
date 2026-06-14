import asyncio
import os
import uuid
import random
import string
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_IDS, DOMAIN, PLANS
from db.mongo import users_col, logs_col
from utils.limits import check_quota, get_remaining_uploads
from utils.drive import validate_folder
from utils.queue import add_to_queue
from utils.logger import log_action, bot_instance as logger_bot
from utils.downloader import download_http, download_youtube

# Set logger bot instance
app = Client("gdrive_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
import utils.logger
utils.logger.bot_instance = app

async def get_user(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        referral_code = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        await users_col.insert_one({
            "_id": user_id,
            "plan": "free",
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
/upgrade – Activate premium (if rewards available)"""
    await message.reply(text)

@app.on_message(filters.command("login"))
async def login_cmd(client, message):
    user_id = message.from_user.id
    await get_user(user_id)
    # Ensure DOMAIN has no trailing slash
    domain = DOMAIN.rstrip('/')
    auth_url = f"{domain}/auth/login?user_id={user_id}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔐 Authorize Google Drive", url=auth_url)]])
    await message.reply("Click the button to connect your Google Drive:", reply_markup=kb)

@app.on_message(filters.document | filters.video | filters.audio | filters.photo | filters.voice)
async def handle_file(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return
    folder_id = user.get("custom_folder_id")
    status_msg = await message.reply("⏳ Downloading file...")
    os.makedirs("downloads", exist_ok=True)
    temp_path = f"downloads/{user_id}_{uuid.uuid4()}.tmp"
    try:
        # FIX: use file_name= instead of file_path=
        file_path = await client.download_media(message, file_name=temp_path)
        filename = (getattr(message.document, 'file_name', None) or
                    getattr(message.video, 'file_name', None) or
                    getattr(message.audio, 'file_name', None) or
                    f"file_{message.message_id}")
        file_size = (message.document.file_size if message.document else
                     message.video.file_size if message.video else
                     message.audio.file_size if message.audio else
                     message.photo[0].file_size if message.photo else 0)
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
        await download_http(url, file_path)
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
    allowed, msg = await check_quota(user_id)
    if not allowed:
        await message.reply(msg)
        return
    folder_id = user.get("custom_folder_id")
    status_msg = await message.reply("⏳ Downloading YouTube video (may take a while)...")
    os.makedirs("downloads", exist_ok=True)
    temp_template = f"downloads/{user_id}_{uuid.uuid4()}_%(title)s.%(ext)s"
    try:
        final_path = await download_youtube(url, temp_template)
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

@app.on_message(filters.command("myplan"))
async def myplan_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    plan = user.get("plan", "free")
    remaining = await get_remaining_uploads(user_id)
    rewards = user.get("referral_rewards", 0)
    text = f"**Your Plan:** {plan.upper()}\n**Daily Uploads Left:** {remaining}\n**Referral Rewards:** {rewards} days premium"
    if rewards > 0:
        text += "\n\nType /upgrade to activate premium days."
    await message.reply(text)

@app.on_message(filters.command("upgrade"))
async def upgrade_cmd(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    rewards = user.get("referral_rewards", 0)
    if rewards > 0:
        await users_col.update_one({"_id": user_id}, {"$set": {"plan": "premium", "referral_rewards": 0}})
        await message.reply(f"🎉 Upgraded to Premium for {rewards} days! Enjoy 50 daily uploads.")
    else:
        await message.reply("No reward days available. Invite friends using /referral to earn premium.")

@app.on_message(filters.command("stats"))
async def stats_cmd(client, message):
    user_id = message.from_user.id
    # Count successful uploads from logs
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
