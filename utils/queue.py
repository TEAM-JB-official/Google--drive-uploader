import asyncio
import os
from utils.drive import upload_file_to_drive

user_queues = {}
user_semaphores = {}

async def upload_progress_callback(current, total, reply_func):
    percent = (current * 100) // total if total else 0
    bar = "█" * (percent // 10) + "░" * (10 - (percent // 10))
    try:
        await reply_func(f"📤 Uploading...\n{bar} {percent}%")
    except:
        pass

async def worker(user_id):
    sem = user_semaphores.get(user_id)
    if not sem:
        sem = asyncio.Semaphore(2)
        user_semaphores[user_id] = sem
    queue = user_queues[user_id]
    while True:
        task = await queue.get()
        file_path, filename, folder_id, reply_func, status_msg_id = task
        async with sem:
            # Define progress callback that uses reply_func
            async def progress_cb(current, total):
                await upload_progress_callback(current, total, reply_func)
            
            link, error = await upload_file_to_drive(
                user_id, file_path, filename, folder_id,
                progress_callback=progress_cb   # <-- pass the async callback
            )
            if error:
                new_text = f"❌ Upload failed: {error}"
            else:
                new_text = f"✅ Uploaded: {filename}\n🔗 {link}"
            try:
                await reply_func(new_text)
            except Exception as e:
                if "MESSAGE_NOT_MODIFIED" not in str(e):
                    print(f"Queue edit error: {e}")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
        queue.task_done()

def add_to_queue(user_id, file_path, filename, folder_id, reply_func, status_msg_id=None):
    if user_id not in user_queues:
        user_queues[user_id] = asyncio.Queue()
        asyncio.create_task(worker(user_id))
    user_queues[user_id].put_nowait((file_path, filename, folder_id, reply_func, status_msg_id))
