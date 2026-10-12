import os
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

UPLOAD_URL = "https://upload.gofile.io/uploadfile"


def upload_guest(file_path, progress_callback):
    file_size = os.path.getsize(file_path)
    with open(file_path, "rb") as file_handle:
        encoder = MultipartEncoder(fields={"file": (os.path.basename(file_path), file_handle, "application/octet-stream")})
        last_report = {"at": 0.0}

        def monitor_callback(monitor):
            import time
            now = time.monotonic()
            if now - last_report["at"] >= 0.8 or monitor.bytes_read >= monitor.len:
                last_report["at"] = now
                progress_callback(min(monitor.bytes_read, file_size), file_size)

        monitor = MultipartEncoderMonitor(encoder, monitor_callback)
        response = requests.post(
            UPLOAD_URL,
            data=monitor,
            headers={"Content-Type": monitor.content_type},
            timeout=(30, 3600),
        )
        response.raise_for_status()
        payload = response.json()

    if payload.get("status") != "ok":
        raise RuntimeError("GoFile rejected the upload: " + str(payload.get("status", "unknown status")))
    data = payload.get("data") or {}
    link = data.get("downloadPage")
    if not link and data.get("code"):
        link = "https://gofile.io/d/" + str(data["code"])
    if not link:
        raise RuntimeError("GoFile upload succeeded but returned no share link.")
    return link
