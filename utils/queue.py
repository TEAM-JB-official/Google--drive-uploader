import asyncio
import os
import time
from utils.drive import upload_file_to_drive
from utils.downloader import cancel_download

user_queues = {}
user_semaphores = {}
user_tasks = {}  # store asyncio tasks for cancellation

async def upload_progress_callback(current, total, reply_func, start_time, filename):
    percent = (current * 100) // total if total else 0
    bar = "█" * (percent // 10) + "░" * (10 - (percent // 10))
    elapsed = time.time() - start_time
    eta = (elapsed / current) * (total - current) if current > 0 else 0
    text = f"📤 Uploading {filename}...\n{bar} {percent}%\n⏱️ ETA: {int(eta//60)}:{int(eta%60):02d}"
    try:
        await reply_func(text)
    except:
        pass

async def worker(user_id):
    sem = user_semaphores.get(user_id)
    if not sem:
        sem = asyncio.Semaphore(2)
        user_semaphores[user_id] = sem
    queue = user_queues[user_id]
    while True:
        task_id, file_path, filename, folder_id, reply_func, start_time = await queue.get()
        async with sem:
            link, error = await upload_file_to_drive(user_id, file_path, filename, folder_id)
            if error:
                await reply_func(f"❌ Upload failed: {error}")
            else:
                elapsed = time.time() - start_time
                elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))
                size_mb = os.path.getsize(file_path) / 1e6 if os.path.exists(file_path) else 0
                preview_link = link.replace("open", "preview")  # basic preview
                download_link = link
                await reply_func(
                    f"✅ **Successfully uploaded** [{filename}]({link}) ({size_mb:.1f} MB) to Google Drive.\n\n"
                    f"[Preview File]({preview_link}) | [Download File]({download_link})\n\n"
                    f"Process completed in {elapsed_str}"
                )
            # cleanup
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            # remove from user_tasks
            if user_id in user_tasks and task_id in user_tasks[user_id]:
                del user_tasks[user_id][task_id]
        queue.task_done()

def add_to_queue(user_id, file_path, filename, folder_id, reply_func):
    if user_id not in user_queues:
        user_queues[user_id] = asyncio.Queue()
        asyncio.create_task(worker(user_id))
    task_id = str(uuid.uuid4())
    start_time = time.time()
    user_tasks.setdefault(user_id, {})[task_id] = asyncio.current_task()
    user_queues[user_id].put_nowait((task_id, file_path, filename, folder_id, reply_func, start_time))
    return task_id

def cancel_user_task(user_id, task_id):
    if user_id in user_tasks and task_id in user_tasks[user_id]:
        user_tasks[user_id][task_id].cancel()
        return True
    return False
