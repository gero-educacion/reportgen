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
import os
import tempfile
from pathlib import Path
import json
from google.oauth2 import service_account
import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

def get_drive_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds, _ = google.auth.default(scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


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


def download_drive_file(web_view_link: str, filename: str) -> Path:
    """
    Downloads the Drive file to a system temp directory.
    Returns the Path to the downloaded file.

    Raises:
        FileNotFoundError  — if the file no longer exists on Drive (404)
        RuntimeError       — for any other Drive API error
    """
    file_id = _extract_file_id(web_view_link)
    logger.info("⬇️  Downloading Drive file_id=%s (%s)", file_id, filename)

    service = get_drive_service()

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