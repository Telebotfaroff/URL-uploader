import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from plugins.javdl_core.extractor_javtiful import JavtifulExtractor

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
}
EXTRACTOR = JavtifulExtractor()


def _safe_name(value):
    value = re.sub(r"[\x00-\x1f]", "_", str(value or "video"))
    value = re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .")
    return (value or "video")[:140]


def _pick_quality(qualities):
    def rank(item):
        label = str(item[0])
        match = re.search(r"(\d{3,4})p", label, re.I)
        return int(match.group(1)) if match else (1 if label.lower() == "source" else 0)
    return sorted(qualities.items(), key=rank, reverse=True)[0]


def download_video(video_url, download_dir, progress_callback):
    video = EXTRACTOR.extract(video_url)
    if not video.qualities:
        raise RuntimeError("Javtiful did not expose a downloadable media stream for this video.")
    quality, media_url = _pick_quality(video.qualities)
    os.makedirs(download_dir, exist_ok=True)
    path = Path(download_dir) / (f"{_safe_name(video.title)}_{time.time_ns()}.mp4")
    headers = dict(HEADERS)
    headers["Referer"] = video_url

    if re.search(r"\.m3u8(?:[?#]|$)", media_url, re.I):
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items()),
            "-i", media_url, "-map", "0:v:0", "-map", "0:a:0?",
            "-c", "copy", "-bsf:a", "aac_adtstoasc", str(path),
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            while process.poll() is None:
                size = path.stat().st_size if path.exists() else 0
                progress_callback(size, 0)
                time.sleep(1.5)
            if process.returncode:
                stderr = process.stderr.read().decode("utf-8", "replace")[-1200:]
                raise RuntimeError("FFmpeg could not download this stream: " + stderr)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stderr:
                process.stderr.close()
    else:
        last_error = None
        for attempt in range(2):
            try:
                with requests.get(media_url, stream=True, timeout=(30, 120), headers=headers) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", 0) or 0)
                    done = 0
                    with path.open("wb") as output:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            output.write(chunk)
                            done += len(chunk)
                            progress_callback(done, total)
                last_error = None
                break
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                path.unlink(missing_ok=True)
                if attempt == 0:
                    time.sleep(1)
        if last_error:
            raise RuntimeError(f"HTTP download failed: {last_error}") from last_error

    if not path.exists() or path.stat().st_size <= 0:
        path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file is empty.")
    video.local_path = str(path)
    return video, path, quality
