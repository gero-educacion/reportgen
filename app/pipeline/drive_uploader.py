from google.oauth2 import service_account
import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload, MediaIoBaseDownload
import os
from pathlib import Path
import logging
import json
import io

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds, _ = google.auth.default(scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def upload_pdf_to_drive(
    pdf_path: Path,
    target_folder_id: str,
    filename: str,
):
    """Uploads any file to Drive. Used for PDFs and raw JSON payloads."""
    try:
        logger.info(f"📤 Drive API create file: {filename}")
        service = get_drive_service()

        file_metadata = {
            "name": filename,
            "parents": [target_folder_id],
        }

        ext = Path(pdf_path).suffix.lower()
        mimetype = {
            ".pdf":  "application/pdf",
            ".json": "application/json",
            ".png":  "image/png",
        }.get(ext, "application/octet-stream")

        media = MediaFileUpload(
            str(pdf_path),
            mimetype=mimetype,
            resumable=False,
        )

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        ).execute()

        logger.info("file uploaded ✅")
        return file["webViewLink"]

    except Exception:
        logger.exception("❌ Drive upload failed")
        raise


# ---------------------------------------------------------------------------
# Historic JSON upsert — only used when upload_historic=True
# Merges new payload into existing file if found, creates fresh if not.
# ---------------------------------------------------------------------------

def _find_existing_file(service, filename: str, folder_id: str) -> str | None:
    """Returns the file ID if a file with this name exists in the folder, else None."""
    try:
        results = service.files().list(
            q=f"name='{filename}' and '{folder_id}' in parents and trashed=false",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = results.get("files", [])
        return files[0]["id"] if files else None
    except Exception:
        logger.warning("Could not search Drive for existing file, will create new")
        return None


def upsert_json_to_drive(data: dict, filename: str, folder_id: str) -> str:
    """
    If a file with this name already exists in the folder, downloads it,
    merges the new data on top, and re-uploads in place.
    If not, creates a fresh file.
    Returns the webViewLink.
    """
    try:
        logger.info(f"📤 Upsert historic JSON: {filename}")
        service = get_drive_service()

        existing_id = _find_existing_file(service, filename, folder_id)

        if existing_id:
            logger.info(f"🔀 Merging into existing file id={existing_id}")
            request = service.files().get_media(fileId=existing_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

            existing_data = json.loads(fh.getvalue().decode("utf-8"))
            merged = {**existing_data, **data}
            json_bytes = json.dumps(merged, indent=2, ensure_ascii=False).encode("utf-8")

            media = MediaIoBaseUpload(
                io.BytesIO(json_bytes),
                mimetype="application/json",
                resumable=False,
            )
            file = service.files().update(
                fileId=existing_id,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            ).execute()

        else:
            logger.info("✨ No existing file found, creating fresh historic JSON")
            json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
            file_metadata = {
                "name":     filename,
                "parents":  [folder_id],
                "mimeType": "application/json",
            }
            media = MediaIoBaseUpload(
                io.BytesIO(json_bytes),
                mimetype="application/json",
                resumable=False,
            )
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            ).execute()

        logger.info(f"✅ Historic JSON upserted → {file['webViewLink']}")
        return file["webViewLink"]

    except Exception:
        logger.exception("❌ Historic JSON upsert failed")
        raise