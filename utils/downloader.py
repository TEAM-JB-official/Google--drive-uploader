import aiohttp
import yt_dlp
import asyncio
import os
import uuid

# Limits for free tier (adjust as needed)
MAX_SIZE_MB = 100          # 100 MB max file size
MAX_DURATION_SECS = 600    # 10 minutes max duration

async def download_http(url, dest_path, progress_callback=None):
    """
    Download a file from an HTTP/HTTPS URL using streaming (2 MB chunks).
    progress_callback: sync function (current_bytes, total_bytes)
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            with open(dest_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(2 * 1024 * 1024):  # 2 MB chunks
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        progress_callback(downloaded, total)
    return dest_path

async def download_youtube(url, dest_template, progress_callback=None):
    """
    Stream YouTube video/audio to disk with size/duration limits.
    Uses yt-dlp only for metadata, then downloads via aiohttp.
    """
    # Extract metadata without downloading
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'no_check_certificate': True,
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise Exception(f"Failed to extract info: {e}")

    # Apply size and duration limits
    duration = info.get('duration', 0)
    if duration > MAX_DURATION_SECS:
        raise Exception(f"Video too long: {duration}s (max {MAX_DURATION_SECS}s)")
    # Use filesize_approx if filesize not available
    file_size = info.get('filesize') or info.get('filesize_approx') or 0
    if file_size > MAX_SIZE_MB * 1024 * 1024:
        raise Exception(f"File too large: {file_size/(1024*1024):.1f} MB (max {MAX_SIZE_MB} MB)")

    # Select the best audio format (small) with a direct download URL
    formats = info.get('formats', [])
    selected_format = None
    # First try audio-only (best quality, but usually small)
    for f in formats:
        if f.get('acodec') != 'none' and f.get('url') and 'm3u8' not in f.get('protocol', ''):
            selected_format = f
            break
    # If no audio, fallback to worst video (very small)
    if not selected_format:
        for f in formats:
            if f.get('vcodec') != 'none' and f.get('url') and 'm3u8' not in f.get('protocol', ''):
                selected_format = f
                break
    if not selected_format:
        raise Exception("No suitable direct download format found (try a different video)")

    direct_url = selected_format['url']
    # Determine file extension
    is_audio = selected_format.get('acodec') != 'none' and selected_format.get('vcodec') == 'none'
    ext = '.mp3' if is_audio else '.mp4'
    safe_title = "".join(c for c in info.get('title', 'video') if c.isalnum() or c in ' ._-')[:50]
    unique = uuid.uuid4().hex[:8]
    # Use dest_template directory
    dest_dir = os.path.dirname(dest_template)
    if not dest_dir:
        dest_dir = '.'
    final_path = os.path.join(dest_dir, f"{safe_title}_{unique}{ext}")

    # Download the file using streaming (chunked) – no memory spike
    await download_http(direct_url, final_path, progress_callback)
    return final_path
