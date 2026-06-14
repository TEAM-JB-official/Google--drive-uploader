from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
import requests
from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, REDIRECT_URI, DOMAIN
from db.mongo import users_col
from utils.drive import add_drive_account

app = FastAPI()
BASE_DOMAIN = DOMAIN.rstrip('/') if DOMAIN else ""

@app.get("/success.html", response_class=HTMLResponse)
async def success_page():
    return """
    <!DOCTYPE html>
    <html><head><title>Success</title></head>
    <body style="text-align:center;background:#4CAF50;color:white;padding:50px">
        <h1>✅ Authentication Successful</h1>
        <p>You may close this window and return to Telegram.</p>
    </body></html>
    """

@app.get("/auth/login")
async def auth_login(user_id: int, action: str = "add"):
    # Build Google OAuth URL manually
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive.file",
        "access_type": "offline",
        "prompt": "consent",
        "state": f"{user_id}|{action}",
    }
    from urllib.parse import urlencode
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(params)
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
async def auth_callback(code: str, state: str = None):
    if not state:
        raise HTTPException(400, "Missing state")
    parts = state.split("|")
    try:
        user_id = int(parts[0])
        action = parts[1] if len(parts) > 1 else "add"
    except:
        raise HTTPException(400, "Invalid state")

    # Exchange code for token using direct POST
    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = requests.post("https://oauth2.googleapis.com/token", data=data)
    if resp.status_code != 200:
        raise HTTPException(500, f"Token exchange failed: {resp.text}")
    token_info = resp.json()
    creds_dict = {
        "token": token_info["access_token"],
        "refresh_token": token_info.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
    }

    # Get user email
    headers = {"Authorization": f"Bearer {token_info['access_token']}"}
    userinfo = requests.get("https://www.googleapis.com/oauth2/v1/userinfo", headers=headers).json()
    email = userinfo.get("email")

    if action == "add":
        await add_drive_account(user_id, creds_dict, email)

    # ---------- Send Telegram success message ----------
    try:
        from pyrogram import Client
        from config import API_ID, API_HASH, BOT_TOKEN
        async with Client("temp_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN) as temp_bot:
            user = await temp_bot.get_users(user_id)
            first_name = user.first_name
            await temp_bot.send_message(
                user_id,
                f"**Hi {first_name}**\n\n"
                f"Sign In was successful!\n\n"
                f"You've successfully linked your (**{email}**) Google Drive."
            )
    except Exception as e:
        print(f"Could not send login success message: {e}")

    return RedirectResponse(url=f"{BASE_DOMAIN}/success.html")

@app.get("/health")
async def health():
    return {"status": "ok"}
