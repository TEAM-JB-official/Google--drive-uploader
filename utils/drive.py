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

async def get_drive_service(user_id, email=None):
    """Get service for a specific linked email, or active drive if None."""
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return None
    drives = user.get("drive_tokens", [])
    if not drives:
        return None
    if email:
        for d in drives:
            if d.get("email") == email:
                creds_dict = d["token"]
                break
        else:
            return None
    else:
        # use first drive as active (or implement active_drive field)
        creds_dict = drives[0]["token"]
    creds = Credentials.from_authorized_user_info(creds_dict)
    if creds.expired and creds.refresh_token:
        def refresh():
            creds.refresh(GoogleRequest())
        await asyncio.to_thread(refresh)
        # update stored token
        for i, d in enumerate(drives):
            if d.get("email") == email or (email is None and i == 0):
                drives[i]["token"] = creds_to_dict(creds)
                await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})
                break
    return build("drive", "v3", credentials=creds)

async def get_drive_stats(user_id, email=None):
    """Return {'total': bytes, 'used': bytes, 'trash': bytes} or None."""
    service = await get_drive_service(user_id, email)
    if not service:
        return None
    try:
        about = await asyncio.to_thread(
            lambda: service.about().get(fields="storageQuota").execute()
        )
        quota = about.get("storageQuota", {})
        total = int(quota.get("limit", 0))
        used = int(quota.get("usage", 0))
        trash = int(quota.get("usageInTrash", 0))
        return {"total": total, "used": used, "trash": trash}
    except:
        return None

async def add_drive_account(user_id, creds_dict, email):
    """Add a new Drive account token to user's list."""
    user = await users_col.find_one({"_id": user_id})
    drives = user.get("drive_tokens", [])
    # avoid duplicate emails
    for i, d in enumerate(drives):
        if d.get("email") == email:
            drives[i]["token"] = creds_dict
            break
    else:
        drives.append({"email": email, "token": creds_dict})
    await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})

async def remove_drive_account(user_id, email):
    """Remove a Drive account by email."""
    user = await users_col.find_one({"_id": user_id})
    drives = user.get("drive_tokens", [])
    drives = [d for d in drives if d.get("email") != email]
    await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})

async def get_user_drives(user_id):
    """Return list of emails of linked accounts."""
    user = await users_col.find_one({"_id": user_id})
    return [d.get("email") for d in user.get("drive_tokens", [])]

async def upload_file_to_drive(user_id, file_path, filename, folder_id=None, email=None):
    """Upload to specific account by email, or first account if None."""
    service = await get_drive_service(user_id, email)
    if not service:
        return None, "No linked Google account. Use /log_in first."
    media = MediaFileUpload(file_path, resumable=True, chunksize=1024*1024*5)
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

async def validate_folder(user_id, folder_url, email=None):
    match = re.search(r"folders/([a-zA-Z0-9_-]+)", folder_url)
    if not match:
        return None, "Invalid folder URL"
    folder_id = match.group(1)
    service = await get_drive_service(user_id, email)
    if not service:
        return None, "No linked Google account"
    try:
        def get_folder():
            service.files().get(fileId=folder_id, fields="id").execute()
        await asyncio.to_thread(get_folder)
        return folder_id, None
    except Exception as e:
        return None, f"Cannot access folder: {str(e)}"
