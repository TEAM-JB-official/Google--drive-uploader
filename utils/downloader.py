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
    Download a YouTube video using yt-dlp.
    Supports cookies.txt for age-restricted videos.
    Place cookies.txt in the bot root directory.
    """
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes', 1)
            downloaded = d.get('downloaded_bytes', 0)
            if progress_callback and total:
                progress_callback(downloaded, total)

    # Check for cookies.txt
    cookies_file = "cookies.txt"
    cookies_arg = {"cookiefile": cookies_file} if os.path.exists(cookies_file) else {}

    # List of client options to try (Android, TV, Web)
    clients = [
        {'player_client': ['android'], 'skip': ['hls', 'dash']},
        {'player_client': ['tv'], 'skip': ['hls', 'dash']},
        {'player_client': ['web'], 'skip': ['hls', 'dash']},
    ]

    last_exception = None
    for client in clients:
        ydl_opts = {
            'outtmpl': dest_template,
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'no_check_certificate': True,
            'user_agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36',
            'extractor_args': {'youtube': client},
            'progress_hooks': [progress_hook] if progress_callback else [],
            **cookies_arg,  # add cookies if available
        }
        def run(ydl_opts=ydl_opts):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                    return ydl.prepare_filename(info)
                except Exception as e:
                    raise e
        try:
            loop = asyncio.get_running_loop()
            final_path = await loop.run_in_executor(None, run)
            return final_path
        except Exception as e:
            last_exception = e
            if "Sign in to confirm" not in str(e) and "bot" not in str(e).lower():
                raise
            continue

    # If all fail, try once more with cookies only (if not already used)
    if not cookies_arg and os.path.exists("cookies.txt"):
        # Retry with cookies (should have been used already, but just in case)
        ydl_opts = {
            'outtmpl': dest_template,
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'no_check_certificate': True,
            'user_agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36',
            'extractor_args': {'youtube': {'player_client': ['android']}},
            'progress_hooks': [progress_hook] if progress_callback else [],
            'cookiefile': 'cookies.txt',
        }
        def run2():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                    return ydl.prepare_filename(info)
                except Exception as e:
                    raise e
        try:
            loop = asyncio.get_running_loop()
            final_path = await loop.run_in_executor(None, run2)
            return final_path
        except Exception as e:
            last_exception = e

    raise Exception(f"YouTube download failed: {str(last_exception or 'Unknown error')}\nIf the video is age-restricted, please place a cookies.txt file (exported from your browser) in the bot root directory.")
