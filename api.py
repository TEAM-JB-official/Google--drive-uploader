from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os
from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, REDIRECT_URI, DOMAIN
from db.mongo import users_col

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Serve success page
@app.get("/success.html", response_class=HTMLResponse)
async def success_page():
    return """
    <html>
    <head><title>Google Drive Bot - Success</title></head>
    <body>
    <h1>✅ Authentication Successful</h1>
    <p>You may close this window and return to Telegram bot.</p>
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
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
    # Encode user_id in state
    return RedirectResponse(url=f"{auth_url}&state={user_id}")

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str = None):
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
    # Get user email
    from google.oauth2.credentials import Credentials
    creds_obj = Credentials.from_authorized_user_info(creds_dict)
    service = build("drive", "v3", credentials=creds_obj)
    about = service.about().get(fields="user").execute()
    email = about["user"]["emailAddress"]
    # Store in DB
    await users_col.update_one(
        {"_id": user_id},
        {"$set": {"drive_tokens": creds_dict, "email": email}},
        upsert=True
    )
    return RedirectResponse(url=f"{DOMAIN}/success.html")

@app.get("/health")
async def health():
    return {"status": "ok"}
