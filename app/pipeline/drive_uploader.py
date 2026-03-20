from google.oauth2 import service_account
import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import os
from pathlib import Path
import logging
import json

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


from googleapiclient.http import MediaFileUpload

def upload_pdf_to_drive(
    pdf_path: Path,
    target_folder_id: str,
    filename: str,
):
    try:
        logger.info(f"📤 Drive API create file: {filename}")

        logger.info("uploading file to google drive...")
        service = get_drive_service()

        file_metadata = {
            "name": filename,
            "parents": [target_folder_id],
        }

        media = MediaFileUpload(
            str(pdf_path),
            mimetype="application/pdf",
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
    
    except Exception as e:
        logger.exception("❌ Drive upload failed")
        raise

    
