import aiohttp
import yt_dlp
import asyncio
import os
import uuid
import re

# No size or duration limits – keep original quality

async def download_http_stream(url, dest_path, progress_callback=None):
    """
    Stream a file from an HTTP/HTTPS URL directly to disk in 2 MB chunks.
    progress_callback: sync function (downloaded_bytes, total_bytes)
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

def _best_format(formats):
    """
    Select the best format that is a single, direct‑download file
    (no HLS/DASH merging). Prefers combined video+audio if available.
    """
    # First, look for a direct MP4 with both video and audio
    for f in formats:
        if (f.get('protocol') == 'https' and
            f.get('acodec') != 'none' and
            f.get('vcodec') != 'none' and
            f.get('ext') in ['mp4', 'mkv'] and
            not f.get('manifest_url')):
            return f
    # Fallback: best video (without audio) – later we will download audio separately and merge
    # But we want to avoid merging to save memory, so we still try to find an audio+video format
    # Last resort: any direct https format
    for f in formats:
        if f.get('protocol') == 'https' and not f.get('manifest_url'):
            return f
    return None

async def download_youtube(url, dest_template, progress_callback=None):
    """
    YouTube downloader with true streaming – no RAM spike.
    - Extracts metadata with yt-dlp.
    - Selects a single direct‑download format (avoids merging).
    - Downloads via aiohttp in 2 MB chunks.
    - Maintains original quality.
    - Uses cookies if present (for age‑restricted videos).
    """
    # 1. Extract info without downloading
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'no_check_certificate': True,
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
        'format_sort': ['res', 'vcodec:h264', 'acodec:aac'],  # prefer good combined formats
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise Exception(f"Failed to extract info: {e}")

    # 2. Find a suitable format (single file, no merging)
    formats = info.get('formats', [])
    selected = _best_format(formats)

    # If no perfect format, try to get a direct URL from the 'best' format
    if not selected:
        # Re‑run with format='best' to get a direct URL
        with yt_dlp.YoutubeDL({'quiet': True, 'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None}) as ydl2:
            try:
                # Get the direct URL for the best format
                best_info = ydl2.extract_info(url, download=False)
                best_formats = best_info.get('formats', [])
                selected = _best_format(best_formats)
            except:
                pass

    if not selected:
        raise Exception("No direct download URL found (video may require merging, which is not supported to save memory).")

    direct_url = selected['url']
    # Determine file extension from the selected format
    ext = selected.get('ext', 'mp4')
    # Clean filename
    title = re.sub(r'[^\w\s.-]', '', info.get('title', 'video'))[:50]
    unique_id = uuid.uuid4().hex[:8]
    filename = f"{title}_{unique_id}.{ext}"
    # Use the same directory as dest_template
    dest_dir = os.path.dirname(dest_template) or '.'
    final_path = os.path.join(dest_dir, filename)

    # 3. Download using streaming (2 MB chunks)
    await download_http_stream(direct_url, final_path, progress_callback)
    return final_path
