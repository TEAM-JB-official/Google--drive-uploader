import asyncio
import os
from utils.drive import upload_file_to_drive

user_queues = {}
user_semaphores = {}

async def worker(user_id):
    sem = user_semaphores.get(user_id)
    if not sem:
        sem = asyncio.Semaphore(2)  # max concurrent per user (adjustable)
        user_semaphores[user_id] = sem
    queue = user_queues[user_id]
    while True:
        task = await queue.get()
        # task = (file_path, filename, folder_id, reply_func, status_msg_id)
        file_path, filename, folder_id, reply_func, status_msg_id = task
        async with sem:
            link, error = await upload_file_to_drive(user_id, file_path, filename, folder_id)
            if error:
                await reply_func(f"❌ Upload failed: {error}")
            else:
                await reply_func(f"✅ Uploaded: {filename}\n🔗 {link}")
            # Delete local file
            if os.path.exists(file_path):
                os.remove(file_path)
        queue.task_done()

def add_to_queue(user_id, file_path, filename, folder_id, reply_func, status_msg_id=None):
    if user_id not in user_queues:
        user_queues[user_id] = asyncio.Queue()
        asyncio.create_task(worker(user_id))
    user_queues[user_id].put_nowait((file_path, filename, folder_id, reply_func, status_msg_id))
