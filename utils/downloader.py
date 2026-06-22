import aiohttp
import yt_dlp
import asyncio
import os
import uuid
import re

async def download_http_stream(url, dest_path, progress_callback=None):
    """
    Stream a file directly to disk in 2 MB chunks.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            with open(dest_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(2 * 1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        progress_callback(downloaded, total)
    return dest_path

def _find_direct_format(formats):
    """
    Find a single file format (not DASH) with both audio and video.
    """
    for f in formats:
        if (f.get('protocol') == 'https' and
            f.get('acodec') != 'none' and
            f.get('vcodec') != 'none' and
            f.get('ext') in ['mp4', 'mkv', 'webm'] and
            not f.get('manifest_url')):
            return f
    # Fallback: any direct HTTPS format (could be video-only or audio-only)
    for f in formats:
        if f.get('protocol') == 'https' and not f.get('manifest_url'):
            return f
    return None

async def download_youtube(url, dest_template, progress_callback=None):
    """
    YouTube downloader with true streaming and fallback to yt-dlp merge.
    - No size/duration limits.
    - Keeps original quality.
    - Uses direct streaming when possible.
    - Falls back to yt-dlp with ffmpeg merging if needed (still disk-bound).
    """
    # 1. Extract metadata without downloading
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

    formats = info.get('formats', [])

    # 2. Try to find a single direct format
    direct_fmt = _find_direct_format(formats)
    if direct_fmt:
        # Use streaming download
        direct_url = direct_fmt['url']
        ext = direct_fmt.get('ext', 'mp4')
        title = re.sub(r'[^\w\s.-]', '', info.get('title', 'video'))[:50]
        unique = uuid.uuid4().hex[:8]
        filename = f"{title}_{unique}.{ext}"
        dest_dir = os.path.dirname(dest_template) or '.'
        final_path = os.path.join(dest_dir, filename)
        await download_http_stream(direct_url, final_path, progress_callback)
        return final_path

    # 3. No direct format – fallback to yt-dlp's built-in download (uses ffmpeg merge)
    # This writes directly to disk, memory usage remains low.
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes', 1)
            downloaded = d.get('downloaded_bytes', 0)
            if progress_callback:
                progress_callback(downloaded, total)

    ydl_opts_dl = {
        'outtmpl': dest_template,
        'format': 'bestvideo+bestaudio/best',   # best quality combined
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'no_check_certificate': True,
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
        'progress_hooks': [progress_hook] if progress_callback else [],
        'concurrent_fragment_downloads': 1,     # avoid memory spikes
        'cache': False,
    }
    def run():
        with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    loop = asyncio.get_running_loop()
    final_path = await loop.run_in_executor(None, run)
    return final_path
