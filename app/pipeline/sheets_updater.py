"""
sheets_updater.py
-----------------
Writes one status row per student to a Google Sheet.

Batching strategy:
  - update_student_status() (called by every job) only RPUSHes the row onto a
    Redis list — no Sheets call inside the job. Calling the Sheet per job, from
    many workers at once, made every call hang until the 60s timeout.
  - flush_status_buffer() drains that list in batch: one read of the Student ID
    column, one batchUpdate for students that already have a row (update in
    place, no duplicates) and one append for new ones.
  - run_status_flush_loop() runs in a side process of every worker (see
    worker.py); a Redis lock makes sure only one replica flushes at a time.
  - Exponential backoff + jitter on every Sheets API call (handles quota errors).

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
import uuid
import httplib2
from google_auth_httplib2 import AuthorizedHttp
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES    = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_TAB = "Status"

STATUS_BUFFER         = "sheet_status_rows"
STATUS_FLUSH_LOCK     = "sheet_status_flush_lock"
STATUS_FLUSH_INTERVAL = int(os.environ.get("STATUS_FLUSH_INTERVAL", 60))
SHEETS_HTTP_TIMEOUT   = 120

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
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=SHEETS_HTTP_TIMEOUT))
    return build("sheets", "v4", http=http, cache_discovery=False)


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


def _read_student_ids(service, spreadsheet_id: str) -> list[str]:
    """Column B (Student ID) top to bottom; index 0 is the header row."""
    result = _with_backoff(lambda: (
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{SHEET_TAB}!B:B",
            majorDimension="COLUMNS",
        ).execute()
    ))
    values = result.get("values") or [[]]
    return [str(v).strip() for v in values[0]]


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
    Queues this student's status row for the Google Sheet (one Redis RPUSH).
    flush_status_buffer() writes it within ~STATUS_FLUSH_INTERVAL seconds.
    """
    if not os.environ.get("SHEETS_STATUS_ID"):
        logger.warning("SHEETS_STATUS_ID not set, skipping sheet update")
        return

    from app.queue import get_redis

    try:
        row_data = _build_row(
            student_id, student, status,
            drive_links or {}, input_json_link,
            email_sent, drive_uploaded, siteground_uploaded,
            error_msg,
        )
        get_redis().rpush(
            STATUS_BUFFER,
            json.dumps({"student_id": str(student_id).strip(), "row": row_data}, ensure_ascii=False),
        )
        logger.info(f"📊 Sheet row queued: student={student_id} status={status}")
    except Exception:
        logger.exception("⚠️ Failed to queue status sheet row (non-fatal)")


def flush_status_buffer(r) -> int:
    """
    Writes every queued status row to the sheet in batch. Returns rows written.
    If the Sheets API fails, the rows stay in the processing list and the
    next run retries them first.
    """
    spreadsheet_id = os.environ.get("SHEETS_STATUS_ID")
    if not spreadsheet_id:
        return 0

    processing = f"{STATUS_BUFFER}:processing"
    if not r.exists(processing):
        if not r.exists(STATUS_BUFFER):
            return 0
        r.rename(STATUS_BUFFER, processing)  # atomic: new rows go to a fresh list

    # Last row per student wins (an "error" followed by an "ok" keeps "ok").
    latest: dict[str, list] = {}
    for item in r.lrange(processing, 0, -1):
        entry = json.loads(item)
        latest[entry["student_id"]] = entry["row"]

    service  = get_sheets_service()
    existing = _read_student_ids(service, spreadsheet_id)

    if not existing or not existing[0]:
        _with_backoff(lambda: (
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{SHEET_TAB}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": [HEADERS]},
            ).execute()
        ))
        existing = [HEADERS[1]]

    row_of = {sid: idx + 1 for idx, sid in enumerate(existing) if idx > 0}  # 1-based
    last_col = chr(64 + TOTAL_COLS)

    updates = [
        {"range": f"{SHEET_TAB}!A{row_of[sid]}:{last_col}{row_of[sid]}", "values": [row]}
        for sid, row in latest.items() if sid in row_of
    ]
    new_rows = [row for sid, row in latest.items() if sid not in row_of]

    if updates:
        _with_backoff(lambda: (
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": updates},
            ).execute()
        ))
    if new_rows:
        _with_backoff(lambda: (
            service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=f"{SHEET_TAB}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": new_rows},
            ).execute()
        ))

    r.delete(processing)
    logger.info(f"✅ Status sheet flushed: {len(updates)} updated, {len(new_rows)} appended")
    return len(updates) + len(new_rows)


def run_status_flush_loop():
    """
    Runs forever in a side process of each worker (see worker.py). Every
    STATUS_FLUSH_INTERVAL seconds, whichever replica grabs the Redis lock
    flushes the buffer; the others skip that round.
    """
    from redis import Redis
    from app.queue import REDIS_URL

    r = Redis.from_url(REDIS_URL, socket_connect_timeout=10, socket_timeout=30)
    token = uuid.uuid4().hex
    logger.info("📊 Status sheet flush loop started (every %ss)", STATUS_FLUSH_INTERVAL)

    while True:
        time.sleep(STATUS_FLUSH_INTERVAL)
        try:
            # Lock outlives a slow flush (HTTP timeout x backoff) with margin.
            if not r.set(STATUS_FLUSH_LOCK, token, nx=True, ex=900):
                continue
            try:
                flush_status_buffer(r)
            finally:
                if r.get(STATUS_FLUSH_LOCK) == token.encode():
                    r.delete(STATUS_FLUSH_LOCK)
        except Exception:
            logger.exception("⚠️ Status sheet flush failed, retrying next round")
