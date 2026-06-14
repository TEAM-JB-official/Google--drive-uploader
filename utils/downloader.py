import aiohttp
import yt_dlp
import asyncio
import os

async def download_http(url, dest_path, progress_callback=None):
    """
    Download a file from an HTTP/HTTPS URL.
    progress_callback: a synchronous function that accepts (current, total)
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            with open(dest_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(1024*1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        progress_callback(downloaded, total)
    return dest_path

async def download_youtube(url, dest_template, progress_callback=None):
    """
    Download a YouTube video using yt-dlp.
    progress_callback: a synchronous function that accepts (downloaded_bytes, total_bytes)
    """
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes', 1)
            downloaded = d.get('downloaded_bytes', 0)
            if progress_callback and total:
                progress_callback(downloaded, total)

    ydl_opts = {
        'outtmpl': dest_template,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook] if progress_callback else [],
    }
    def run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    loop = asyncio.get_running_loop()
    final_path = await loop.run_in_executor(None, run)
    return final_path

async def download_drive_file(client, message, service, file_id, original_filename, file_size, status_msg):
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    os.makedirs("downloads", exist_ok=True)
    # Use extremely short random filename (8 chars + extension)
    ext = os.path.splitext(original_filename)[1] or ".bin"
    random_name = f"{uuid.uuid4().hex[:8]}{ext}"
    temp_path = f"downloads/{user_id}_{random_name}"
    
    try:
        task_id = str(uuid.uuid4())
        def progress_sync(current, total):
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(
                _drive_download_progress(current, total, status_msg, task_id),
                loop
            )
        async def download():
            request = service.files().get_media(fileId=file_id)
            with open(temp_path, "wb") as f:
                downloader = MediaIoBaseDownload(f, request, chunksize=1024*1024*5)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    if status:
                        progress_sync(status.resumable_progress, status.total_size)
        await asyncio.to_thread(download)
        await status_msg.edit_text("📤 Sending file to Telegram...")
        await client.send_document(
            chat_id=user_id,
            document=temp_path,
            caption=f"✅ **Downloaded from Drive:**\n`{original_filename}`\n📏 Size: {os.path.getsize(temp_path)/1e6:.2f} MB"
        )
        await status_msg.delete()
        os.remove(temp_path)
    except asyncio.CancelledError:
        await status_msg.edit_text("❌ Download cancelled.")
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed: {str(e) if str(e) else 'Unknown error'}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
