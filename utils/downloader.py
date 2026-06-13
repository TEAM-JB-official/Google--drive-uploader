import aiohttp
import yt_dlp
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
                    if progress_callback:
                        await progress_callback(downloaded, total)
    return dest_path

async def download_youtube(url, dest_path):
    # dest_path is a template, yt-dlp will add extension
    ydl_opts = {
        'outtmpl': dest_path,
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
    }
    def run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    loop = asyncio.get_running_loop()
    final_path = await loop.run_in_executor(None, run)
    return final_path
