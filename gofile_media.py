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
            "-c", "copy", "-bsf:a", "aac_adtstoasc",
            "-progress", "pipe:1", "-nostats", str(path),
        ]
        duration = max(0.0, float(video.duration or 0))
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        try:
            for line in process.stdout:
                if line.startswith("out_time_ms=") and duration > 0:
                    try:
                        elapsed_seconds = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    progress_callback(min(elapsed_seconds, duration), duration, "time")
            process.wait(timeout=900)
            if process.returncode:
                raise RuntimeError(f"FFmpeg could not download this stream (exit code {process.returncode}).")
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            path.unlink(missing_ok=True)
            raise RuntimeError("FFmpeg download timed out after 900 seconds.") from exc
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout:
                process.stdout.close()
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
