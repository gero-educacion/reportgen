"""
db_writer.py
------------
1. write_majors_to_db()  — upserts 4 majors for any student into byw_autoconocimiento
2. post_utp_payload()    — looks up lead_id from byw_tracking_algoritmo_AC by email,
                           POSTs to UTP CRM endpoint with careers + report links,
                           then writes the returned validationId back to that same table.

UTP endpoint payload shape:
  {
    "leadId":            "<lead_id from byw_tracking_algoritmo_AC>",
    "career1":           "<Carrera 1>",
    "career2":           "<Carrera 2>",
    "resultsLink":       "<SiteGround public URL — estudiante report>",
    "resultsLinkPadres": "<SiteGround public URL — padres report>"
  }

Required env vars (MySQL):
  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

Required env vars (UTP endpoint):
  UTP_ENDPOINT_URL   e.g. https://staging2.geroeducacion.com/scripts/mock_utp_endpoint.php
  UTP_API_KEY        (optional)
"""

import os
import logging
import requests

from app.pipeline.db import get_connection as _get_connection

logger = logging.getLogger(__name__)


# def _get_lead_id(email: str) -> str | None:
#     """
#     Looks up lead_id for this student in byw_usuarios_habilitados by email.
#     Returns the lead_id string or None if not found.
#     """
#     sql = """
#         SELECT legajo
#         FROM byw_usuarios_habilitados
#         WHERE LOWER(cedula_matricula) = LOWER(%s)
#         AND cliente = "Universidad Tecnológica de Perú"
#     """
#     try:
#         conn = _get_connection()
#         with conn:
#             with conn.cursor() as cur:
#                 cur.execute(sql, (email,))
#                 row = cur.fetchone()
#         if row:
#             logger.info("✅ Found lead_id=%s for %s", row["legajo"], email)
#             return str(row["legajo"])
#         else:
#             return "failed"
#     except Exception as e:
#         logger.exception("⚠️  Failed to look up lead_id for %s", email)
#         return None

def _write_validation_id(email: str, validation_id: str):
    """
    Writes the validationId returned by the UTP endpoint into
    byw_tracking_algoritmo_AC, updating the most recent row for this email.
    """
    sql = """
        UPDATE byw_tracking_algoritmo_AC
        SET    validationId = %s
        WHERE  LOWER(email) = LOWER(%s)
        ORDER  BY response_at DESC
        LIMIT  1
    """
    try:
        conn = _get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (validation_id, email))
            conn.commit()
        logger.info("✅ validationId=%s written to byw_tracking_algoritmo_AC for %s", validation_id, email)
    except Exception as e:
        logger.exception("⚠️  Failed to write validationId for %s (non-fatal)", email)

def alter_table_reports(user_email: str, links: dict):
    """
    Updates reporte_estudiante and reporte_padres in byw_tracking_algoritmo_AC
    for the most recent row matching this email.
    """
    sql = """
        UPDATE byw_tracking_algoritmo_AC
        SET    reporte_estudiante = %s,
               reporte_padres     = %s
        WHERE  LOWER(email) = LOWER(%s)
        ORDER  BY response_at DESC
        LIMIT  1
    """
    reporte_estudiante = links.get("estudiante", "")
    reporte_padres     = links.get("padres", "")

    try:
        conn = _get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (reporte_estudiante, reporte_padres, user_email))
                rows_affected = cur.rowcount
            conn.commit()
        if rows_affected == 0:
            logger.warning("alter_table_reports: no row found for email=%s", user_email)
        else:
            logger.info("✅ Links written to byw_tracking_algoritmo_AC for %s", user_email)
    except Exception as e:
        logger.exception("⚠️  Failed to write links for %s (non-fatal)", user_email)

# ---------------------------------------------------------------------------
# write_majors_to_db
# ---------------------------------------------------------------------------

def write_majors_to_db(student: dict):
    """
    Upserts the student's top 4 majors into byw_autoconocimiento.
    Matches the WP user by email (case-insensitive).
    """
    email = (student.get("Email") or student.get("email") or "").strip()
    if not email:
        logger.warning("write_majors_to_db: no email, skipping")
        return

    c1 = (student.get("CARRERA_01") or student.get("Carrera 01"))
    c2 = (student.get("CARRERA_02") or student.get("Carrera 02"))
    c3 = (student.get("CARRERA_03") or student.get("Carrera 03"))
    c4 = (student.get("CARRERA_04") or student.get("Carrera 04"))

    sql = """
        INSERT INTO byw_autoconocimiento
          (user_id, user_email, nombre, carrera_1, carrera_2, carrera_3, carrera_4)
        SELECT
          u.ID,
          u.user_email,
          u.display_name,
          %s, %s, %s, %s
        FROM byw_users AS u
        WHERE LOWER(u.user_email) = LOWER(%s)
        ON DUPLICATE KEY UPDATE
          user_email = VALUES(user_email),
          nombre     = VALUES(nombre),
          carrera_1  = VALUES(carrera_1),
          carrera_2  = VALUES(carrera_2),
          carrera_3  = VALUES(carrera_3),
          carrera_4  = VALUES(carrera_4)
    """

    try:
        conn = _get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (c1, c2, c3, c4, email))
            conn.commit()
        logger.info("✅ Majors written to DB for %s", email)
    except Exception as e:
        logger.exception("⚠️  Failed to write majors to DB for %s (non-fatal)", email)


# ---------------------------------------------------------------------------
# post_utp_payload
# ---------------------------------------------------------------------------

def post_utp_payload(
    student_id: str,
    user_email: str,
    student: dict,
    report_links: dict,     # {"estudiante": "https://...", "padres": "https://..."}
):
    """
    1. Looks up lead_id from byw_tracking_algoritmo_AC by email.
    2. POSTs leadId + career1 + career2 + resultsLink to UTP CRM endpoint.
    3. Captures validationId from the response and writes it back to the table.
    """
    endpoint = os.environ.get("UTP_ENDPOINT_URL", "").strip().strip('"')
    if not endpoint:
        logger.warning("UTP_ENDPOINT_URL not set — skipping")
        return
 
    api_key = os.environ.get("UTP_API_KEY", "").strip()
    if not api_key:
        logger.warning("UTP_API_KEY not set — request will likely be rejected")
  
    # Look up lead_id — fall back to student_id if not found
    # lead_id = _get_lead_id(user_email) if user_email else None
    # if lead_id == "failed":
    #     logger.warning("⚠️  FAILED, FALLING BACK TO USER_EMAIL =%s", user_email)
    #     lead_id = user_email

    c1 = (student.get("CARRERA_01") or student.get("Carrera 01"))
    telefono = student.get("Telefono") or ""
    email = student.get("Email_formulario") or ""
 
    payload = {
        "leadId":      user_email,
        "career1":     c1,
        "career2":     "",
        "resultsLink":       report_links.get("estudiante", ""),
        "resultsLinkParent": report_links.get("padres", ""),
        "telephone": telefono,
        "email": email
    }
 
    headers = {
        "Content-Type": "application/json",
        "x-api-key":    api_key,
    }
 
    logger.info(
        "📨 Posting UTP payload | endpoint=%s | leadId=%s | career1=%s | career2=%s | resultsLink=%s | resultsLinkParent=%s",
        endpoint, user_email, payload["career1"], payload["career2"], payload["resultsLink"], payload["resultsLinkParent"],
    )
 
    try:
        r = requests.post(endpoint, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        body = r.json()
        logger.info("✅ UTP endpoint response: %s", body)
 
        if not body.get("success"):
            logger.warning("⚠️  UTP endpoint returned success=false: %s", body)
 
        # Capture validationId and persist to byw_tracking_algoritmo_AC
        validation_id = body.get("validationId") or body.get("validation_id") or body.get("id")
        if validation_id:
            logger.info("🔖 validationId received: %s", validation_id)
            if user_email:
                _write_validation_id(user_email, str(validation_id))
        else:
            logger.warning("⚠️ No validationId in UTP response: %s", body)
 
    except Exception as e:
        try:
            logger.error("⚠️  UTP response body: %s", r.text)
        except Exception:
            pass
        logger.exception("⚠️  UTP endpoint post failed (non-fatal)")


def write_sg_message_id(user_email: str, sg_message_id: str):
    """
    Called by reportgen right after sending the UTP student email.
    Stamps sg_message_id + initial email_status="Enviado" onto the most
    recent row for this email. This is the ONLY write that matches by email —
    everything after this matches by sg_message_id instead, to avoid stamping
    the wrong row if the student gets reprocessed later.
    """
    from datetime import date

    sql = """
        UPDATE byw_tracking_algoritmo_AC
        SET    sg_message_id = %s,
               email_status = %s,
               email_status_updated_at = %s
        WHERE  LOWER(email) = LOWER(%s)
        ORDER  BY response_at DESC
        LIMIT  1
    """
    try:
        conn = _get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (sg_message_id, "Enviado", date.today(), user_email))
                rows_affected = cur.rowcount
            conn.commit()
        if rows_affected == 0:
            logger.warning("write_sg_message_id: no row found for email=%s", user_email)
        else:
            logger.info("✅ sg_message_id=%s written for %s", sg_message_id, user_email)
    except Exception:
        logger.exception("⚠️  Failed to write sg_message_id for %s (non-fatal)", user_email)


# Rank covers both raw webhook event names and Activity API status
# values, since some historic code paths use the latter.
EMAIL_STATUS_RANK = {
    'Enviado':      -1,
    'processed':     0,
    'deferred':      0,
    'delivered':     1,
    'open':          1,
    'click':         1,
    'not_delivered': 2,
    'bounce':        2,
    'blocked':       2,
    'dropped':       2,
    'invalid':       2,
    'spamreport':    2,
    'unsubscribe':   2,
    'group_unsubscribe': 2,
}

EMAIL_EVENTS_BUFFER = "email_status_events"


def buffer_email_events(events: list[dict]) -> int:
    """
    Called by the SendGrid webhook. Pushes "<base_sg_message_id>|<event>" for
    every ranked event onto a Redis list in one round trip — no DB work in
    the request. flush_email_status_buffer() applies them in batch later.
    Returns the number of events buffered.
    """
    from app.queue import get_redis

    items = []
    for e in events:
        sg_message_id = e.get("sg_message_id") or ""
        event = e.get("event")
        if sg_message_id and event in EMAIL_STATUS_RANK:
            items.append(f"{sg_message_id.split('.')[0]}|{event}")
    if items:
        get_redis().rpush(EMAIL_EVENTS_BUFFER, *items)
    return len(items)


def flush_email_status_buffer() -> int:
    """
    Twice-daily job (Railway cron service, see app/flush_email_status.py).
    Drains the webhook event buffer, keeps the highest-ranked event per
    message, and applies it to byw_tracking_algoritmo_AC with one SELECT
    plus an UPDATE per row that actually changes. A status never moves
    down in rank. Messages with no row (non-UTP emails) are ignored.

    NOTE: this only records status — it does not trigger a resend. The
    auto-resend-on-bounce mechanic was removed (it flooded reportgen and
    its resend_count bookkeeping kept losing track of attempts).

    Returns the number of rows updated.
    """
    from app.queue import get_redis

    r = get_redis()
    processing = f"{EMAIL_EVENTS_BUFFER}:processing"
    # Leftovers from a run that crashed before DB commit get retried first.
    if not r.exists(processing):
        if not r.exists(EMAIL_EVENTS_BUFFER):
            logger.info("📬 email flush: buffer empty")
            return 0
        r.rename(EMAIL_EVENTS_BUFFER, processing)  # atomic: new events go to a fresh list

    latest: dict[str, str] = {}
    raw = r.lrange(processing, 0, -1)
    for item in raw:
        base_id, _, event = item.decode().partition("|")
        if EMAIL_STATUS_RANK.get(event, -1) >= EMAIL_STATUS_RANK.get(latest.get(base_id), -1):
            latest[base_id] = event

    updated = 0
    ids = list(latest)
    conn = _get_connection()
    with conn:
        with conn.cursor() as cur:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                cur.execute(
                    "SELECT sg_message_id, email_status FROM byw_tracking_algoritmo_AC "
                    f"WHERE sg_message_id IN ({', '.join(['%s'] * len(chunk))})",
                    chunk,
                )
                for row in cur.fetchall():
                    new_status = latest[row["sg_message_id"]]
                    current = row["email_status"]
                    if new_status == current:
                        continue
                    if EMAIL_STATUS_RANK[new_status] < EMAIL_STATUS_RANK.get(current, -1):
                        continue
                    cur.execute(
                        "UPDATE byw_tracking_algoritmo_AC "
                        "SET email_status = %s, email_status_updated_at = CURDATE() "
                        "WHERE sg_message_id = %s",
                        (new_status, row["sg_message_id"]),
                    )
                    updated += 1
        conn.commit()

    r.delete(processing)
    logger.info("📬 email flush: %d events, %d messages, %d rows updated", len(raw), len(latest), updated)
    return updated
