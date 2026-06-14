import asyncio
import os
import uuid
import time
from utils.drive import upload_file_to_drive
from utils.logger import log_action

user_queues = {}
user_semaphores = {}
user_tasks = {}

async def worker(user_id):
    sem = user_semaphores.get(user_id)
    if not sem:
        sem = asyncio.Semaphore(2)
        user_semaphores[user_id] = sem
    queue = user_queues[user_id]
    while True:
        task = await queue.get()
        # task = (task_id, file_path, filename, folder_id, reply_func, start_time)
        task_id, file_path, filename, folder_id, reply_func, start_time = task
        async with sem:
            link, error = await upload_file_to_drive(user_id, file_path, filename, folder_id)
            if error:
                new_text = f"❌ Upload failed: {error}"
                # Log failure
                await log_action(user_id, "upload", "failed", filename=filename, error=error)
            else:
                elapsed = time.time() - start_time
                elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))
                # Get actual file size (bytes to MB)
                size_mb = os.path.getsize(file_path) / 1e6 if os.path.exists(file_path) else 0
                preview_link = link.replace("open", "preview")  # simple preview link
                download_link = link
                new_text = (
                    f"✅ **Successfully uploaded** [{filename}]({link}) ({size_mb:.1f} MB) to Google Drive.\n\n"
                    f"[Preview File]({preview_link}) | [Download File]({download_link})\n\n"
                    f"Process completed in {elapsed_str}"
                )
                # Log success with size
                await log_action(user_id, "upload", "success", filename=filename, size_mb=size_mb)
            try:
                await reply_func(new_text)
            except Exception as e:
                if "MESSAGE_NOT_MODIFIED" not in str(e):
                    print(f"Queue edit error: {e}")
            # Clean up local file
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            # Remove task from user_tasks
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
