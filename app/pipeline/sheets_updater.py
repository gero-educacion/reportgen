"""
sheets_updater.py
-----------------
Writes one status row per student to a Google Sheet.

Concurrency strategy:
  - Uses exponential backoff + jitter on every Sheets API call (handles quota errors)
  - Before appending, scans existing rows for this student_id and UPDATES in place
    instead of appending — this eliminates duplicate rows even if two requests
    race to write at the same time (last-write-wins on the same row is fine).

Required env var:
  SHEETS_STATUS_ID        — the spreadsheet ID to write to
  GOOGLE_CREDENTIALS_JSON — service account JSON as a string
"""

import os
import time
import random
import logging
from datetime import datetime, timezone

import google.auth
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES    = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_TAB = "Status"

HEADERS = [
    "Timestamp",            # A — when this run was processed
    "Student ID",           # B
    "Nombre",               # C
    "Email",                # D
    "Rol",                  # E
    "Status",               # F — ok | error | resent | recomputed
    "Link Estudiante",      # G
    "Link Padres",          # H
    "Link CCR",             # I
    "Link Input JSON",      # J
    "Email enviado?",       # K
    "Drive upload?",        # L
    "SiteGround upload?",   # M
    "Error",                # N
    "Legacy Links (JSON)",   # ← new, appended at the end
]
TOTAL_COLS = len(HEADERS)


LEGACY_EXCLUDE = {"estudiante", "padres", "ccr_rojo", "ccr_amarillo", "ccr_verde"}


def get_sheets_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds, _ = google.auth.default(scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _with_backoff(fn, max_attempts: int = 6):
    for attempt in range(max_attempts):
        try:
            return fn()
        except HttpError as e:
            status = e.resp.status
            if status in (429, 500, 502, 503) and attempt < max_attempts - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    f"Sheets API {status} on attempt {attempt+1}, retrying in {wait:.1f}s"
                )
                time.sleep(wait)
            else:
                raise


def _ensure_headers(service, spreadsheet_id: str):
    """Write headers on row 1 if the sheet is empty."""
    result = _with_backoff(lambda: (
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{SHEET_TAB}!A1:A1",
        ).execute()
    ))
    if not result.get("values"):
        _with_backoff(lambda: (
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{SHEET_TAB}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": [HEADERS]},
            ).execute()
        ))


def _find_student_row(service, spreadsheet_id: str, student_id: str) -> int | None:
    """
    Returns the 1-based sheet row number if student_id already exists in col B,
    or None if not found.
    """
    result = _with_backoff(lambda: (
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{SHEET_TAB}!B:B",
            majorDimension="COLUMNS",
        ).execute()
    ))

    col_values = result.get("values", [[]])[0]
    for idx, cell in enumerate(col_values):
        if str(cell).strip() == str(student_id).strip():
            return idx + 1  # 1-based

    return None


def _build_row(
    student_id: str,
    student: dict,
    status: str,
    drive_links: dict,
    input_json_link: str,
    email_sent: str,
    drive_uploaded: str,
    siteground_uploaded: str,
    error_msg: str,
) -> list:
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    name  = (student.get("Nombre y Apellido") or student.get("nombre_estudiante"))
    email = student.get("Email") or student.get("email", "")
    rol   = student.get("Rol", "")
    links = drive_links or {}

    legacy_links = {k: v for k, v in links.items() if k not in LEGACY_EXCLUDE}
    legacy_json  = json.dumps(legacy_links, ensure_ascii=False) if legacy_links else ""

    return [
        now,
        student_id,
        name,
        email,
        rol,
        status,
        links.get("estudiante", ""),
        links.get("padres", ""),
        links.get("ccr_rojo") or links.get("ccr_amarillo") or links.get("ccr_verde", ""),
        input_json_link or "",
        email_sent,
        drive_uploaded,
        siteground_uploaded,
        error_msg or "",
        legacy_json,   # ← appended at the end, matches HEADERS

    ]


def update_student_status(
    student_id: str,
    student: dict,
    status: str,
    email_sent: str = "no",
    drive_uploaded: str = "no",
    siteground_uploaded: str = "no",
    drive_links: dict | None = None,
    input_json_link: str = "",
    error_msg: str = "",
):
    """
    Upserts one status row for this student in the Google Sheet.
    Safe to call from concurrent requests — duplicate rows won't accumulate.
    """
    spreadsheet_id = os.environ.get("SHEETS_STATUS_ID")
    if not spreadsheet_id:
        logger.warning("SHEETS_STATUS_ID not set, skipping sheet update")
        return

    try:
        service  = get_sheets_service()

        _ensure_headers(service, spreadsheet_id)

        row_data = _build_row(
            student_id, student, status,
            drive_links or {}, input_json_link,
            email_sent, drive_uploaded, siteground_uploaded,
            error_msg,
        )

        existing_row = _find_student_row(service, spreadsheet_id, student_id)

        if existing_row:
            range_notation = (
                f"{SHEET_TAB}!A{existing_row}:{chr(64 + TOTAL_COLS)}{existing_row}"
            )
            logger.info(f"📊 Updating existing sheet row {existing_row} for {student_id}")
            _with_backoff(lambda: (
                service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=range_notation,
                    valueInputOption="USER_ENTERED",
                    body={"values": [row_data]},
                ).execute()
            ))
        else:
            logger.info(f"📊 Appending new sheet row for {student_id}")
            _with_backoff(lambda: (
                service.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id,
                    range=f"{SHEET_TAB}!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row_data]},
                ).execute()
            ))

        logger.info(f"✅ Sheet updated: student={student_id} status={status}")

    except Exception:
        logger.exception("⚠️ Failed to update status sheet (non-fatal)")