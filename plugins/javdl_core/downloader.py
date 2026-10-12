from pathlib import Path
import re
import subprocess
import requests

from .quality import choose_quality


class Downloader:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(self, workdir="downloads", chunk_size=1024 * 1024, retries=3):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.chunk_size = chunk_size
        self.retries = max(1, int(retries))

    def download(self, video, quality="best", progress=None):
        selected, url = choose_quality(video.qualities, quality)
        video.selected_quality = selected
        path = self.workdir / (self._filename(video.title, selected) + ".mp4")

        headers = {"User-Agent": self.USER_AGENT}
        if video.source_url:
            headers["Referer"] = video.source_url

        if self._is_hls(url):
            self._download_hls(url, path, headers, progress)
        else:
            self._download_http(url, path, headers, progress)

        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError("Downloader produced an empty file")

        video.local_path = str(path)
        return path

    def _download_http(self, url, path, headers, progress):
        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                with requests.get(
                    url,
                    stream=True,
                    timeout=(30, 120),
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", 0) or 0)
                    done = 0
                    if progress:
                        progress(0, total, "download")

                    with path.open("wb") as file:
                        for chunk in response.iter_content(chunk_size=self.chunk_size):
                            if not chunk:
                                continue
                            file.write(chunk)
                            done += len(chunk)
                            if progress:
                                progress(done, total, "download")
                return
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                path.unlink(missing_ok=True)

        raise RuntimeError(
            f"HTTP download failed after {self.retries} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _download_hls(url, path, headers, progress):
        if progress:
            progress(0, 0, "download")
        header_text = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
        command = [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-headers", header_text,
            "-i", url,
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            str(path),
        ]

        try:
            subprocess.run(command, check=True)
        except FileNotFoundError as exc:
            raise RuntimeError("FFmpeg is required to download HLS (.m3u8) streams") from exc
        except subprocess.CalledProcessError as exc:
            path.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg HLS download failed with exit code {exc.returncode}") from exc

        if progress:
            progress(1, 1, "download")

    @staticmethod
    def _is_hls(url):
        return bool(re.search(r"\.m3u8(?:[?#]|$)", str(url), re.I))

    @staticmethod
    def _filename(title, quality):
        return re.sub(
            r"[^A-Za-z0-9._ -]+",
            "_",
            f"{title or 'video'} [{quality}]",
        ).strip()[:180]
