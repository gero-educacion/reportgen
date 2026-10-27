"""
status_db.py
------------
Writes one status row per student to MySQL (byw_reportgen_status), replacing
the Google Sheet. Same columns and same behavior as the sheet: one row per
Student ID, the latest run wins.

Batching strategy:
  - update_student_status() (called by every job) only RPUSHes the row onto a
    Redis list. That list is the single bucket shared by every worker and
    every replica.
  - run_status_db_flush_loop() runs in a side process of every worker (see
    worker.py). Every STATUS_DB_POLL_INTERVAL seconds it checks the bucket and
    flushes when it holds STATUS_DB_BATCH_SIZE rows, or when the oldest row
    has waited STATUS_DB_MAX_WAIT seconds, whichever comes first. A Redis lock
    makes sure only one replica flushes at a time.
  - flush_status_db_buffer() writes the whole batch with one
    INSERT ... ON DUPLICATE KEY UPDATE per chunk, in a single transaction.

DUAL WRITE (temporary): while SHEETS_STATUS_ID is set, every row is also
pushed onto the sheet's buffer, which sheets_updater keeps flushing as before.
To retire the sheet, unset SHEETS_STATUS_ID, then delete sheets_updater.py
and the two marked spots (here and in worker.py).

Table DDL: scripts/sql/byw_reportgen_status.sql
"""

import os
import time
import json
import uuid
import logging
from datetime import datetime, timezone

from app.pipeline.db import db_cursor

logger = logging.getLogger(__name__)

STATUS_DB_BUFFER      = "status_db_rows"
STATUS_DB_FLUSH_LOCK  = "status_db_flush_lock"
STATUS_DB_BATCH_SIZE  = int(os.environ.get("STATUS_DB_BATCH_SIZE", 100))
STATUS_DB_MAX_WAIT    = int(os.environ.get("STATUS_DB_MAX_WAIT", 600))
STATUS_DB_POLL_INTERVAL = int(os.environ.get("STATUS_DB_POLL_INTERVAL", 15))

TS_FORMAT = "%Y-%m-%d %H:%M:%S UTC"

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
    "Legacy Links (JSON)",  # O
]
TOTAL_COLS = len(HEADERS)

# Table columns in HEADERS order (A..O).
DB_COLUMNS = [
    "processed_at", "student_id", "nombre", "email", "rol", "status",
    "link_estudiante", "link_padres", "link_ccr", "link_input_json",
    "email_enviado", "drive_upload", "siteground_upload",
    "error", "legacy_links_json",
]

UPSERT_SQL = (
    f"INSERT INTO byw_reportgen_status ({', '.join(DB_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(DB_COLUMNS))}) "
    "ON DUPLICATE KEY UPDATE "
    + ", ".join(f"{c} = VALUES({c})" for c in DB_COLUMNS if c != "student_id")
)

LEGACY_EXCLUDE = {"estudiante", "padres", "ccr_rojo", "ccr_amarillo", "ccr_verde"}


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
    now   = datetime.now(timezone.utc).strftime(TS_FORMAT)
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
        legacy_json,
    ]


def row_to_db_params(row: list) -> tuple:
    """Sheet-shaped row (A..O) → params for UPSERT_SQL. Empty strings become NULL."""
    params = [v if v not in ("", None) else None for v in row]
    params[0] = datetime.strptime(row[0], TS_FORMAT) if isinstance(row[0], str) else row[0]
    params[1] = str(row[1]).strip()
    return tuple(params)


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
    Queues this student's status row (one Redis RPUSH per destination).
    flush_status_db_buffer() writes it within STATUS_DB_MAX_WAIT seconds.
    """
    from app.queue import get_redis

    try:
        row_data = _build_row(
            student_id, student, status,
            drive_links or {}, input_json_link,
            email_sent, drive_uploaded, siteground_uploaded,
            error_msg,
        )
        sid = str(student_id).strip()
        r = get_redis()
        r.rpush(
            STATUS_DB_BUFFER,
            json.dumps({"student_id": sid, "row": row_data, "queued_at": time.time()}, ensure_ascii=False),
        )
        # DUAL WRITE — remove together with sheets_updater.py
        if os.environ.get("SHEETS_STATUS_ID"):
            from app.pipeline.sheets_updater import STATUS_BUFFER
            r.rpush(STATUS_BUFFER, json.dumps({"student_id": sid, "row": row_data}, ensure_ascii=False))
        logger.info(f"📊 Status row queued: student={student_id} status={status}")
    except Exception:
        logger.exception("⚠️ Failed to queue status row (non-fatal)")


def flush_status_db_buffer(r) -> int:
    """
    Writes every queued status row to byw_reportgen_status in one transaction.
    Returns rows written. If MySQL fails, the rows stay in the processing
    list and the next flush retries them first.
    """
    processing = f"{STATUS_DB_BUFFER}:processing"
    if not r.exists(processing):
        if not r.exists(STATUS_DB_BUFFER):
            return 0
        r.rename(STATUS_DB_BUFFER, processing)  # atomic: new rows go to a fresh list

    # Last row per student wins (an "error" followed by an "ok" keeps "ok").
    latest: dict[str, list] = {}
    for item in r.lrange(processing, 0, -1):
        entry = json.loads(item)
        latest[entry["student_id"]] = entry["row"]

    params = [row_to_db_params(row) for row in latest.values()]
    with db_cursor() as cur:
        for i in range(0, len(params), STATUS_DB_BATCH_SIZE):
            cur.executemany(UPSERT_SQL, params[i:i + STATUS_DB_BATCH_SIZE])

    r.delete(processing)
    logger.info(f"✅ Status table flushed: {len(params)} rows")
    return len(params)


def _should_flush(r) -> bool:
    """Leftovers from a failed flush, a full bucket, or a row that waited too long."""
    if r.exists(f"{STATUS_DB_BUFFER}:processing"):
        return True
    if r.llen(STATUS_DB_BUFFER) >= STATUS_DB_BATCH_SIZE:
        return True
    oldest = r.lindex(STATUS_DB_BUFFER, 0)
    if oldest is None:
        return False
    return time.time() - json.loads(oldest).get("queued_at", 0) >= STATUS_DB_MAX_WAIT


def run_status_db_flush_loop():
    """
    Runs forever in a side process of each worker (see worker.py). Every
    STATUS_DB_POLL_INTERVAL seconds it checks the shared bucket; when it is
    due, whichever replica grabs the Redis lock flushes it.
    """
    from redis import Redis
    from app.queue import REDIS_URL

    r = Redis.from_url(REDIS_URL, socket_connect_timeout=10, socket_timeout=30)
    token = uuid.uuid4().hex
    logger.info(
        "📊 Status table flush loop started (%s rows or %ss, checked every %ss)",
        STATUS_DB_BATCH_SIZE, STATUS_DB_MAX_WAIT, STATUS_DB_POLL_INTERVAL,
    )

    while True:
        time.sleep(STATUS_DB_POLL_INTERVAL)
        try:
            if not _should_flush(r):
                continue
            if not r.set(STATUS_DB_FLUSH_LOCK, token, nx=True, ex=600):
                continue
            try:
                flush_status_db_buffer(r)
            finally:
                if r.get(STATUS_DB_FLUSH_LOCK) == token.encode():
                    r.delete(STATUS_DB_FLUSH_LOCK)
        except Exception:
            logger.exception("⚠️ Status table flush failed, retrying next round")
