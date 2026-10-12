"""Track active download processes and cooperative upload cancellation requests."""
import asyncio

ACTIVE_PROCESSES = {}
ACTIVE_UPLOADS = set()
CANCEL_REQUESTED = set()


def register_process(user_id, process):
    ACTIVE_PROCESSES[int(user_id)] = process


def unregister_process(user_id, process=None):
    user_id = int(user_id)
    current = ACTIVE_PROCESSES.get(user_id)
    if current is not None and (process is None or current is process):
        ACTIVE_PROCESSES.pop(user_id, None)


def register_upload(user_id):
    ACTIVE_UPLOADS.add(int(user_id))


def unregister_upload(user_id):
    user_id = int(user_id)
    ACTIVE_UPLOADS.discard(user_id)
    CANCEL_REQUESTED.discard(user_id)


def consume_cancel_request(user_id):
    user_id = int(user_id)
    if user_id in CANCEL_REQUESTED:
        CANCEL_REQUESTED.discard(user_id)
        return True
    return False


async def cancel_process(user_id):
    user_id = int(user_id)
    process = ACTIVE_PROCESSES.get(user_id)
    if process is not None and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        return True
    if user_id in ACTIVE_UPLOADS:
        CANCEL_REQUESTED.add(user_id)
        return True
    return False
