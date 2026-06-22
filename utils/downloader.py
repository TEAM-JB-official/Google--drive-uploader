import aiohttp
import yt_dlp
import asyncio
import os

async def download_http(url, dest_path, progress_callback=None):
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
    YouTube downloader with memory optimisation and cookies support.
    progress_callback: sync function (downloaded_bytes, total_bytes)
    """
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes', 1)
            downloaded = d.get('downloaded_bytes', 0)
            if progress_callback and total:
                progress_callback(downloaded, total)

    cookies_arg = {"cookiefile": "cookies.txt"} if os.path.exists("cookies.txt") else {}

    ydl_opts = {
        'outtmpl': dest_template,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'no_check_certificate': True,
        'user_agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36',
        'extractor_args': {'youtube': {'player_client': ['android'], 'skip': ['hls', 'dash']}},
        'progress_hooks': [progress_hook] if progress_callback else [],
        'concurrent_fragment_downloads': 1,   # reduce memory usage
        'cache': False,
        **cookies_arg,
    }
    def run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, run)
    except Exception as e:
        # Fallback to TV client if Android fails
        if "Sign in to confirm" in str(e) or "bot" in str(e).lower():
            ydl_opts['extractor_args']['youtube']['player_client'] = ['tv']
            return await loop.run_in_executor(None, run)
        raise
