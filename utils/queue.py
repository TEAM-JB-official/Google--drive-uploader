import asyncio
import os
from utils.drive import upload_file_to_drive

user_queues = {}
user_semaphores = {}

async def worker(user_id):
    sem = user_semaphores.get(user_id)
    if not sem:
        sem = asyncio.Semaphore(2)   # max concurrent uploads per user
        user_semaphores[user_id] = sem
    queue = user_queues[user_id]
    while True:
        task = await queue.get()
        # task = (file_path, filename, folder_id, reply_func, status_msg_id)
        file_path, filename, folder_id, reply_func, status_msg_id = task
        async with sem:
            link, error = await upload_file_to_drive(user_id, file_path, filename, folder_id)
            # Prepare result message
            if error:
                new_text = f"❌ Upload failed: {error}"
            else:
                new_text = f"✅ Uploaded: {filename}\n🔗 {link}"
            # Safely edit the status message – avoid MESSAGE_NOT_MODIFIED
            try:
                # reply_func is typically status_msg.edit_text
                await reply_func(new_text)
            except Exception as e:
                # Ignore error if it's just "message not modified"
                if "MESSAGE_NOT_MODIFIED" not in str(e):
                    print(f"Queue edit error for user {user_id}: {e}")
            # Clean up local file
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
