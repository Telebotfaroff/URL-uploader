"""Track active yt-dlp subprocesses so a user can cancel their own download."""
import asyncio

ACTIVE_PROCESSES = {}


def register_process(user_id, process):
    ACTIVE_PROCESSES[int(user_id)] = process


def unregister_process(user_id, process=None):
    user_id = int(user_id)
    current = ACTIVE_PROCESSES.get(user_id)
    if current is not None and (process is None or current is process):
        ACTIVE_PROCESSES.pop(user_id, None)


async def cancel_process(user_id):
    process = ACTIVE_PROCESSES.get(int(user_id))
    if process is None or process.returncode is not None:
        return False
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
    return True
