
import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import os
import time
import requests
from plugins.functions.display_progress import humanbytes

def DetectFileSize(url):
    with requests.get(url, allow_redirects=True, stream=True, timeout=(10, 60)) as r:
        r.raise_for_status()
        return int(r.headers.get("content-length", 0))


def DownLoadFile(url, file_name, chunk_size, client, ud_type, message_id, chat_id):
    if os.path.exists(file_name):
        os.remove(file_name)
    if not url:
        return file_name
    with requests.get(url, allow_redirects=True, stream=True, timeout=(10, 60)) as r:
        r.raise_for_status()
        total_size = int(r.headers.get("content-length", 0))
        downloaded_size = 0
        with open(file_name, 'wb') as fd:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                fd.write(chunk)
                downloaded_size += len(chunk)
                if client is not None and (downloaded_size == len(chunk) or downloaded_size % max(chunk_size * 20, 1) < len(chunk)):
                    time.sleep(0.3)
                    try:
                        client.edit_message_text(
                            chat_id,
                            message_id,
                            text="{}: {} of {}".format(
                                ud_type,
                                humanbytes(downloaded_size),
                                humanbytes(total_size)
                            )
                        )
                    except Exception:
                        pass
    return file_name
