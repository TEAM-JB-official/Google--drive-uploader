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

async def migrate_old_tokens(user_id):
    """Convert old string token format to new dict format."""
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return
    tokens = user.get("drive_tokens", [])
    new_tokens = []
    changed = False
    for item in tokens:
        if isinstance(item, str):
            # Old format: just token string
            new_tokens.append({"email": "unknown@old.token", "token": item})
            changed = True
        elif isinstance(item, dict):
            new_tokens.append(item)
    if changed:
        await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": new_tokens}})

async def get_drive_service(user_id, email=None):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return None
    await migrate_old_tokens(user_id)
    user = await users_col.find_one({"_id": user_id})
    drives = user.get("drive_tokens", [])
    if not drives:
        return None
    # Filter only dict items
    dict_drives = [d for d in drives if isinstance(d, dict)]
    if not dict_drives:
        return None
    if email:
        for d in dict_drives:
            if d.get("email") == email:
                creds_dict = d["token"]
                break
        else:
            return None
    else:
        creds_dict = dict_drives[0]["token"]
    creds = Credentials.from_authorized_user_info(creds_dict)
    if creds.expired and creds.refresh_token:
        def refresh():
            creds.refresh(GoogleRequest())
        await asyncio.to_thread(refresh)
        # update stored token
        for i, d in enumerate(dict_drives):
            if d.get("email") == email or (email is None and i == 0):
                dict_drives[i]["token"] = creds_to_dict(creds)
                # Merge back into drives list
                new_drives = []
                for old in user.get("drive_tokens", []):
                    if isinstance(old, dict) and old.get("email") == dict_drives[i]["email"]:
                        new_drives.append(dict_drives[i])
                    else:
                        new_drives.append(old)
                await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": new_drives}})
                break
    return build("drive", "v3", credentials=creds)

async def get_drive_stats(user_id, email=None):
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
    user = await users_col.find_one({"_id": user_id})
    await migrate_old_tokens(user_id)
    drives = user.get("drive_tokens", [])
    # replace if email exists
    replaced = False
    for i, d in enumerate(drives):
        if isinstance(d, dict) and d.get("email") == email:
            drives[i] = {"email": email, "token": creds_dict}
            replaced = True
            break
    if not replaced:
        drives.append({"email": email, "token": creds_dict})
    await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})

async def remove_drive_account(user_id, email):
    user = await users_col.find_one({"_id": user_id})
    drives = user.get("drive_tokens", [])
    new_drives = [d for d in drives if not (isinstance(d, dict) and d.get("email") == email)]
    await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": new_drives}})

async def get_user_drives(user_id):
    user = await users_col.find_one({"_id": user_id})
    await migrate_old_tokens(user_id)
    user = await users_col.find_one({"_id": user_id})
    tokens = user.get("drive_tokens", [])
    emails = []
    for item in tokens:
        if isinstance(item, dict):
            emails.append(item.get("email", "Unknown"))
        elif isinstance(item, str):
            emails.append("Unknown (old token)")
    return emails

async def upload_file_to_drive(user_id, file_path, filename, folder_id=None, email=None):
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
