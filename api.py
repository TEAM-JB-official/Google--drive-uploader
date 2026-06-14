from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os
import traceback
from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, REDIRECT_URI, DOMAIN
from db.mongo import users_col

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
BASE_DOMAIN = DOMAIN.rstrip('/') if DOMAIN else ""

# Try to import add_drive_account; if it fails, print the error
try:
    from utils.drive import add_drive_account
    print("✅ add_drive_account imported successfully")
except Exception as e:
    print(f"❌ Failed to import add_drive_account: {e}")
    traceback.print_exc()
    # Fallback definition (should never be needed if drive.py is correct)
    async def add_drive_account(user_id, creds_dict, email):
        print(f"FALLBACK: adding account for {user_id} with email {email}")
        user = await users_col.find_one({"_id": user_id})
        if not user:
            await users_col.insert_one({"_id": user_id, "drive_tokens": []})
        drives = user.get("drive_tokens", [])
        drives = [d for d in drives if not (isinstance(d, dict) and d.get("email") == email)]
        drives.append({"email": email, "token": creds_dict})
        await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})

@app.get("/success.html", response_class=HTMLResponse)
async def success_page():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Success</title></head>
    <body style="text-align:center;font-family:Arial;padding:50px;background:#4CAF50;color:white;">
        <h1>✅ Authentication Successful</h1>
        <p>You may close this window and return to Telegram.</p>
    </body>
    </html>
    """

@app.get("/auth/login")
async def auth_login(user_id: int, action: str = "add"):
    try:
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
        auth_url, _ = flow.authorization_url(
            prompt="consent",
            access_type="offline",
            include_granted_scopes="true",
            state=f"{user_id}|{action}"
        )
        return RedirectResponse(auth_url)
    except Exception as e:
        print(f"Login error: {e}")
        traceback.print_exc()
        raise HTTPException(500, f"Login error: {str(e)}")

@app.get("/auth/callback")
async def auth_callback(code: str, state: str = None):
    try:
        if not state:
            raise HTTPException(400, "Missing state")
        parts = state.split("|")
        try:
            user_id = int(parts[0])
            action = parts[1] if len(parts) > 1 else "add"
        except:
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
        if action == "add":
            await add_drive_account(user_id, creds_dict, email)
        return RedirectResponse(url=f"{BASE_DOMAIN}/success.html")
    except Exception as e:
        print(f"Callback error: {e}")
        traceback.print_exc()
        raise HTTPException(500, f"Callback error: {str(e)}")

@app.get("/health")
async def health():
    return {"status": "ok"}
