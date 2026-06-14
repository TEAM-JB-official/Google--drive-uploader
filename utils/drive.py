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

async def clean_invalid_tokens(user_id):
    """Remove any drive token entry that is not a dict or lacks required token fields."""
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return 0
    tokens = user.get("drive_tokens", [])
    valid_tokens = []
    for item in tokens:
        if not isinstance(item, dict):
            continue
        token_data = item.get("token")
        if not isinstance(token_data, dict):
            continue
        # Minimum required fields for a usable token
        if "token" in token_data and "refresh_token" in token_data:
            valid_tokens.append(item)
    removed = len(tokens) - len(valid_tokens)
    if removed > 0:
        await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": valid_tokens}})
    return removed

async def count_valid_drives(user_id):
    """Return number of properly stored Google Drive accounts (dict with valid token)."""
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return 0
    valid = 0
    for item in user.get("drive_tokens", []):
        if not isinstance(item, dict):
            continue
        token = item.get("token")
        if isinstance(token, dict) and "token" in token and "refresh_token" in token:
            valid += 1
    return valid

async def get_drive_service(user_id, email=None):
    await clean_invalid_tokens(user_id)
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return None
    drives = user.get("drive_tokens", [])
    # Keep only dict entries with valid token dict
    valid_drives = []
    for d in drives:
        if not isinstance(d, dict):
            continue
        token_data = d.get("token")
        if isinstance(token_data, dict) and "token" in token_data:
            valid_drives.append(d)
    if not valid_drives:
        return None
    if email:
        selected = None
        for d in valid_drives:
            if d.get("email") == email:
                selected = d
                break
        if not selected:
            return None
        creds_dict = selected["token"]
    else:
        creds_dict = valid_drives[0]["token"]
    try:
        creds = Credentials.from_authorized_user_info(creds_dict)
    except Exception:
        # Invalid token – remove this drive entry
        for i, d in enumerate(drives):
            if d.get("token") == creds_dict:
                drives.pop(i)
                await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})
                break
        return None
    if creds.expired and creds.refresh_token:
        def refresh():
            creds.refresh(GoogleRequest())
        await asyncio.to_thread(refresh)
        # update stored token
        for i, d in enumerate(valid_drives):
            if d.get("email") == email or (email is None and i == 0):
                d["token"] = creds_to_dict(creds)
                # update in original drives list
                for j, od in enumerate(drives):
                    if isinstance(od, dict) and od.get("email") == d.get("email"):
                        drives[j] = d
                        break
                await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})
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
    except Exception:
        return None

async def add_drive_account(user_id, creds_dict, email):
    await clean_invalid_tokens(user_id)
    user = await users_col.find_one({"_id": user_id})
    drives = user.get("drive_tokens", [])
    # Remove any existing entry with same email
    drives = [d for d in drives if not (isinstance(d, dict) and d.get("email") == email)]
    drives.append({"email": email, "token": creds_dict})
    await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})

async def remove_drive_account(user_id, email):
    user = await users_col.find_one({"_id": user_id})
    drives = user.get("drive_tokens", [])
    drives = [d for d in drives if not (isinstance(d, dict) and d.get("email") == email)]
    await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})

async def get_user_drives(user_id):
    await clean_invalid_tokens(user_id)
    user = await users_col.find_one({"_id": user_id})
    tokens = user.get("drive_tokens", [])
    emails = []
    for item in tokens:
        if isinstance(item, dict):
            email = item.get("email")
            if not email:
                continue
            token_data = item.get("token")
            if isinstance(token_data, dict) and "token" in token_data:
                emails.append(email)
            else:
                emails.append(f"{email} (invalid, re‑login needed)")
        elif isinstance(item, str):
            emails.append("Old token (re‑login needed)")
    return emails

async def upload_file_to_drive(user_id, file_path, filename, folder_id=None, email=None):
    service = await get_drive_service(user_id, email)
    if not service:
        return None, "No valid Google Drive account. Please use /log_in again."
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
        return None, "No valid Google Drive account. Please use /log_in again."
    try:
        def get_folder():
            service.files().get(fileId=folder_id, fields="id").execute()
        await asyncio.to_thread(get_folder)
        return folder_id, None
    except Exception as e:
        return None, f"Cannot access folder: {str(e)}"

async def fix_missing_emails_for_user(user_id):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    user = await users_col.find_one({"_id": user_id})
    if not user:
        return 0
    drives = user.get("drive_tokens", [])
    fixed = 0
    for i, d in enumerate(drives):
        if not isinstance(d, dict):
            continue
        # Skip if already has a valid email
        if d.get("email") and "invalid" not in d["email"] and "old" not in d["email"]:
            continue
        token_data = d.get("token")
        if not isinstance(token_data, dict):
            continue
        try:
            creds = Credentials.from_authorized_user_info(token_data)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            service = build("drive", "v3", credentials=creds)
            about = service.about().get(fields="user").execute()
            real_email = about["user"]["emailAddress"]
            drives[i]["email"] = real_email
            fixed += 1
        except Exception as e:
            print(f"Error fixing email: {e}")
    if fixed > 0:
        await users_col.update_one({"_id": user_id}, {"$set": {"drive_tokens": drives}})
    return fixed
