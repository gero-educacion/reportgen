from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import os
from pathlib import Path
import logging

import logging

logger = logging.getLogger(__name__)


SCOPES = ["https://www.googleapis.com/auth/drive"]

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
        scopes=SCOPES,
    )
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

    
