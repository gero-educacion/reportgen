"""
drive_downloader.py
-------------------
Downloads a file from Google Drive by its webViewLink or file ID,
saves it to a temp path, and returns that path.

The service account already has access because it was the one that uploaded
the file in the first place.
"""

import re
import logging
import tempfile
from pathlib import Path
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

from app.pipeline.drive_uploader import get_drive_service

logger = logging.getLogger(__name__)


def _extract_file_id(web_view_link: str) -> str:
    """
    Extracts the Drive file ID from a webViewLink URL.
    Handles formats like:
      https://drive.google.com/file/d/FILE_ID/view
      https://drive.google.com/open?id=FILE_ID
    """
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", web_view_link)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", web_view_link)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract file ID from link: {web_view_link}")


def download_drive_file(web_view_link: str, filename: str, service=None) -> Path:
    """
    Downloads the Drive file to a system temp directory.
    Returns the Path to the downloaded file.

    Pass an existing `service` (from get_drive_service()) to reuse it across
    multiple downloads in the same job instead of rebuilding it each call.

    Raises:
        FileNotFoundError  — if the file no longer exists on Drive (404)
        RuntimeError       — for any other Drive API error
    """
    file_id = _extract_file_id(web_view_link)
    logger.info("⬇️  Downloading Drive file_id=%s (%s)", file_id, filename)

    service = service or get_drive_service()

    tmp_path = Path(tempfile.mkdtemp()) / filename

    try:
        request = service.files().get_media(fileId=file_id)
        with open(tmp_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

    except HttpError as e:
        if e.resp.status == 404:
            raise FileNotFoundError(
                f"Drive file not found (deleted/moved?): file_id={file_id}"
            ) from e
        raise RuntimeError(f"Drive API error {e.resp.status}: {e}") from e

    if not tmp_path.exists() or tmp_path.stat().st_size < 1000:
        raise RuntimeError(f"Downloaded file looks empty: {tmp_path}")

    logger.info("✅ Downloaded to %s (%d bytes)", tmp_path, tmp_path.stat().st_size)
    return tmp_path