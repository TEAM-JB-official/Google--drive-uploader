import asyncio
import re
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from db.mongo import users_col

def creds_to_dict(creds):
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes
    }

async def get_drive_service(user_id):
    user = await users_col.find_one({"_id": user_id})
    if not user or "drive_tokens" not in user:
        return None
    creds_dict = user["drive_tokens"]
    creds = Credentials.from_authorized_user_info(creds_dict)
    if creds.expired and creds.refresh_token:
        def refresh():
            creds.refresh(GoogleRequest())
        await asyncio.to_thread(refresh)
        await users_col.update_one(
            {"_id": user_id},
            {"$set": {"drive_tokens": creds_to_dict(creds)}}
        )
    return build("drive", "v3", credentials=creds)

async def upload_file_to_drive(user_id, file_path, filename, folder_id=None, progress_callback=None):
    """
    Uploads a file to Google Drive.
    progress_callback: async function(current, total) called during upload.
    """
    service = await get_drive_service(user_id)
    if not service:
        return None, "Not authenticated. Use /login"
    
    # Create media with proper callback
    def sync_callback(current, total):
        # This runs in a thread; we need to schedule an async callback
        if progress_callback:
            asyncio.run_coroutine_threadsafe(progress_callback(current, total), asyncio.get_event_loop())
    
    media = MediaFileUpload(
        file_path,
        resumable=True,
        chunksize=1024*1024*5,
        callback=sync_callback   # <-- correct way
    )
    file_metadata = {"name": filename}
    if folder_id:
        file_metadata["parents"] = [folder_id]
    try:
        def upload():
            return service.files().create(
                body=file_metadata, media_body=media, fields="id, webViewLink"
            ).execute()
        file = await asyncio.to_thread(upload)
        return file.get("webViewLink"), None
    except Exception as e:
        return None, str(e)

async def validate_folder(user_id, folder_url):
    match = re.search(r"folders/([a-zA-Z0-9_-]+)", folder_url)
    if not match:
        return None, "Invalid folder URL"
    folder_id = match.group(1)
    service = await get_drive_service(user_id)
    if not service:
        return None, "Not authenticated"
    try:
        def get_folder():
            service.files().get(fileId=folder_id, fields="id").execute()
        await asyncio.to_thread(get_folder)
        return folder_id, None
    except Exception as e:
        return None, f"Cannot access folder: {str(e)}"
