import os
import logging
from google.oauth2 import service_account
import google.auth
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def get_sheets_service():
    """Get authenticated Google Sheets service"""
    creds, _ = google.auth.default(scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def log_student_reports(
    email: str,
    drive_links: dict,
    spreadsheet_id: str = None,
):
    """
    Escribe una fila en Google Sheets con el email del alumno y los links a sus reportes.
    
    Args:
        email: Email del estudiante
        drive_links: Dict con formato {report_type: drive_link}
        spreadsheet_id: ID de la hoja de cálculo (se obtiene de env var si no se provee)
    """
    try:
        if not spreadsheet_id:
            spreadsheet_id = os.environ.get("SHEETS_LOG_ID")
            if not spreadsheet_id:
                logger.warning("SHEETS_LOG_ID not set, skipping sheets logging")
                return
        
        logger.info(f"📝 Logging to Sheets: {email}")

        service = get_sheets_service()

        # Preparar la fila: [email, link_estudiante, link_padres, link_ccr_rojo, link_ccr_amarillo, link_ccr_verde]
        row = [email]
        
        # Orden consistente de columnas
        report_order = ["estudiante", "padres", "ccr_rojo", "ccr_amarillo", "ccr_verde"]
        
        for report_type in report_order:
            link = drive_links.get(report_type, "")
            row.append(link)

        # Append a la hoja
        range_name = "Sheet1!A:F"  # Ajusta el nombre de la hoja si es necesario
        
        body = {
            "values": [row]
        }

        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()

        logger.info("✅ Successfully logged to Sheets")

    except Exception as e:
        logger.exception("❌ Failed to log to Sheets")
        # No hacemos raise para que no afecte el flujo principal
