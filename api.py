from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os
from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, REDIRECT_URI, DOMAIN
from db.mongo import users_col

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
BASE_DOMAIN = DOMAIN.rstrip('/')   # remove trailing slash if any

# Serve success page (inline HTML, no static file needed)
@app.get("/success.html", response_class=HTMLResponse)
async def success_page():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Google Drive Bot - Success</title></head>
    <body style="text-align:center;font-family:Arial;padding:50px;background:#4CAF50;color:white;">
        <h1>✅ Authentication Successful</h1>
        <p>Your Google Drive is now connected to the bot.</p>
        <p>You may close this window and return to Telegram bot.</p>
        <script>setTimeout(() => window.close(), 4000);</script>
    </body>
    </html>
    """

@app.get("/auth/login")
async def auth_login(user_id: int):
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    # IMPORTANT: pass user_id as the 'state' parameter directly
    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
        state=str(user_id)   # <-- this is the fix
    )
    return RedirectResponse(auth_url)  # no manual &state= appended

@app.get("/auth/callback")
async def auth_callback(code: str, state: str = None):
    if not state:
        raise HTTPException(400, "Missing state")
    try:
        user_id = int(state)
    except ValueError:
        raise HTTPException(400, "Invalid state")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    creds_dict = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes
    }
    from google.oauth2.credentials import Credentials
    creds_obj = Credentials.from_authorized_user_info(creds_dict)
    service = build("drive", "v3", credentials=creds_obj)
    about = service.about().get(fields="user").execute()
    email = about["user"]["emailAddress"]
    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"drive_tokens": creds_dict, "email": email}},
        upsert=True
    )
    return RedirectResponse(url=f"{BASE_DOMAIN}/success.html")  # using BASE_DOMAIN

@app.get("/health")
async def health():
    return {"status": "ok"}
