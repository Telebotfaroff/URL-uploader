"""Upload extracted screenshots to Freeimage.host using API v1."""
import os
import time
from pathlib import Path

import requests

API_URL = "https://freeimage.host/api/1/upload/"


def upload_images(image_paths, api_key=None, progress_callback=None):
    """Upload local image files sequentially and return viewer/direct URLs."""
    api_key = (api_key or os.environ.get("FREEIMAGE_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("FREEIMAGE_API_KEY is required to upload screenshot galleries.")

    paths = [Path(path) for path in image_paths]
    if not paths:
        return []

    uploaded = []
    for index, path in enumerate(paths, 1):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Screenshot file is missing or empty: {path.name}")

        last_error = None
        for attempt in range(2):
            try:
                with path.open("rb") as handle:
                    response = requests.post(
                        API_URL,
                        data={"key": api_key, "action": "upload", "format": "json"},
                        files={"source": (path.name, handle, "image/jpeg")},
                        timeout=(20, 90),
                    )
                response.raise_for_status()
                payload = response.json()
                image = payload.get("image") or {}
                success = payload.get("success") or {}
                if not image.get("url") or (
                    payload.get("status_code") not in (None, 200)
                    or success.get("code") not in (None, 200)
                ):
                    message = (success.get("message") or payload.get("error", {}).get("message") or payload.get("status_txt") or f"unexpected response (status_code={payload.get('status_code')!r}, success_code={success.get('code')!r})")
                    raise RuntimeError(f"Freeimage.host rejected {path.name}: {message}")
                uploaded.append({
                    "url": image["url"],
                    "viewer_url": image.get("url_viewer") or image["url"],
                    "filename": path.name,
                })
                last_error = None
                break
            except RuntimeError:
                # API-declared errors (such as an invalid API key) will not improve by retrying.
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(1)
        if last_error:
            raise RuntimeError(
                f"Could not upload screenshot {index}/{len(paths)}: {type(last_error).__name__}"
            ) from last_error
        if progress_callback:
            try:
                progress_callback(index, len(paths))
            except Exception:
                pass
    return uploaded
