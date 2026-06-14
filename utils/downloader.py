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
