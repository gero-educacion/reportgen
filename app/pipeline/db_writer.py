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

from app.pipeline.db import get_connection as _get_connection, db_cursor

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

def _update_tracking_row(set_sql: str, set_params: tuple, token: str | None, email: str | None) -> str:
    """
    Applies `SET <set_sql>` to the job's own row in byw_tracking_algoritmo_AC.

    For UTP jobs the job's student_id IS the tracking token, so the row is
    matched by `token` (+ client='UTP'). Matching by "the lead's most recent
    row" stamps the wrong copy when a lead has duplicate submissions.

    Falls back to the email match only when NO row has that token. (MySQL's
    rowcount counts changed rows, not matched ones, so 0 can also mean the
    token row already held these values — that must not fall back.)

    Returns "token", "email" or "none" (which match was used). Raises on DB error.
    """
    with db_cursor() as cur:
        if token:
            cur.execute(
                f"UPDATE byw_tracking_algoritmo_AC SET {set_sql} WHERE token = %s AND client = 'UTP'",
                (*set_params, token),
            )
            if cur.rowcount > 0:
                return "token"
            cur.execute(
                "SELECT 1 FROM byw_tracking_algoritmo_AC WHERE token = %s AND client = 'UTP' LIMIT 1",
                (token,),
            )
            if cur.fetchone():
                return "token"  # row exists, values were already set
            logger.warning("No tracking row with token=%s — falling back to email=%s", token, email)
        if not email:
            return "none"
        cur.execute(
            f"UPDATE byw_tracking_algoritmo_AC SET {set_sql} WHERE email = %s ORDER BY response_at DESC LIMIT 1",
            (*set_params, email),
        )
        return "email" if cur.rowcount > 0 else "none"


def _write_validation_id(email: str, validation_id: str, token: str | None = None):
    """
    Writes the validationId returned by the UTP endpoint into the job's row
    of byw_tracking_algoritmo_AC (by token; email only as fallback).
    """
    try:
        matched = _update_tracking_row("validationId = %s", (validation_id,), token, email)
        if matched == "none":
            logger.error("UTP_VALIDATION_ID_NOT_WRITTEN token=%s lead=%s validationId=%s (no row)",
                         token, email, validation_id)
        else:
            logger.info("✅ validationId=%s written (by %s) token=%s lead=%s", validation_id, matched, token, email)
    except Exception:
        # UTP already has this lead: log the id so it can be written by hand.
        logger.exception("UTP_VALIDATION_ID_NOT_WRITTEN token=%s lead=%s validationId=%s (DB error)",
                         token, email, validation_id)

def alter_table_reports(user_email: str, links: dict, token: str | None = None):
    """
    Updates reporte_estudiante and reporte_padres on the job's row of
    byw_tracking_algoritmo_AC (by token; email only as fallback).
    """
    reporte_estudiante = links.get("estudiante", "")
    reporte_padres     = links.get("padres", "")

    try:
        matched = _update_tracking_row(
            "reporte_estudiante = %s, reporte_padres = %s",
            (reporte_estudiante, reporte_padres), token, user_email,
        )
        if matched == "none":
            logger.warning("alter_table_reports: no row found for token=%s email=%s", token, user_email)
        else:
            logger.info("✅ Links written (by %s) token=%s lead=%s", matched, token, user_email)
    except Exception:
        logger.exception("⚠️  Failed to write links for token=%s email=%s (non-fatal)", token, user_email)

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
        WHERE u.user_email = %s
        ON DUPLICATE KEY UPDATE
          user_email = VALUES(user_email),
          nombre     = VALUES(nombre),
          carrera_1  = VALUES(carrera_1),
          carrera_2  = VALUES(carrera_2),
          carrera_3  = VALUES(carrera_3),
          carrera_4  = VALUES(carrera_4)
    """

    try:
        with db_cursor() as cur:
            cur.execute(sql, (c1, c2, c3, c4, email))
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
    1. POSTs leadId + careers + report links to the UTP CRM endpoint, retrying
       transient failures (see _post_utp_with_retry).
    2. Writes the returned validationId onto this job's row (token = student_id).
    Never raises. Returns an outcome string ("ok", "timeout", "rejected_409", ...);
    final failures are logged as "UTP_POST_FAILED token=... lead=...".
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
 
    # UTP's own lead ID when their receiver already returned it (self-login
    # students, set by the algoritmo); otherwise the geroId / Lead ID as before.
    lead_id = (student.get("utp_lead_id") or "").strip() or user_email

    payload = {
        "leadId":      lead_id,
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
        "📨 Posting UTP payload | endpoint=%s | leadId=%s | geroId=%s | career1=%s | career2=%s | resultsLink=%s | resultsLinkParent=%s",
        endpoint, lead_id, user_email, payload["career1"], payload["career2"], payload["resultsLink"], payload["resultsLinkParent"],
    )
 
    # Never raises: tasks.py treats this as non-fatal. An exception would make
    # RQ retry the whole job, which re-runs the pipeline and resends the email.
    try:
        body, outcome = _post_utp_with_retry(endpoint, payload, headers, student_id, user_email)
        if body is None:
            # Distinct, greppable line: these rows need recover_utp_validation_ids.py
            logger.error("UTP_POST_FAILED token=%s lead=%s outcome=%s", student_id, user_email, outcome)
            return outcome

        if not body.get("success"):
            logger.warning("⚠️  UTP endpoint returned success=false token=%s lead=%s: %s",
                           student_id, user_email, body)

        # Capture validationId and persist it on THIS job's row
        validation_id = body.get("validationId") or body.get("validation_id") or body.get("id")
        if validation_id:
            logger.info("🔖 validationId received: %s", validation_id)
            _write_validation_id(user_email, str(validation_id), token=student_id)
            return "ok"
        logger.error("UTP_POST_FAILED token=%s lead=%s outcome=no_validation_id body=%s",
                     student_id, user_email, body)
        return "no_validation_id"
    except Exception:
        logger.exception("UTP_POST_FAILED token=%s lead=%s outcome=unexpected_error", student_id, user_email)
        return "unexpected_error"


# Waits before each retry. 3 attempts in total; worst case with the 20s
# request timeout is ~67s, well under the RQ job timeout (600s).
UTP_RETRY_DELAYS = (2, 5)
UTP_REQUEST_TIMEOUT = 20


def _post_utp_with_retry(endpoint: str, payload: dict, headers: dict, token, lead):
    """
    POSTs to UTP, retrying on timeout / connection error / 5xx / 429.
    4xx (other than 429) means UTP rejected it (bad payload, lead already
    confirmed...) and is NOT retried. Returns (json_body, outcome); body is
    None when every attempt failed.
    """
    import time

    attempts = len(UTP_RETRY_DELAYS) + 1
    outcome = "unknown"
    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=UTP_REQUEST_TIMEOUT)
        except requests.Timeout:
            # Ambiguous: UTP may have saved it and just answered late.
            outcome = "timeout"
            logger.warning("⏱️  UTP POST timeout (ambiguous: UTP may have saved it) token=%s lead=%s attempt=%d/%d",
                           token, lead, attempt, attempts)
        except requests.RequestException as e:
            outcome = "connection_error"
            logger.warning("⚠️  UTP POST error token=%s lead=%s attempt=%d/%d: %s",
                           token, lead, attempt, attempts, e)
        else:
            if r.ok:
                try:
                    body = r.json()
                except ValueError:
                    logger.error("⚠️  UTP returned non-JSON (HTTP %s) token=%s lead=%s body=%s",
                                 r.status_code, token, lead, r.text[:2000])
                    return None, "bad_response"
                logger.info("✅ UTP endpoint response (attempt %d): %s", attempt, body)
                return body, "ok"
            if r.status_code != 429 and r.status_code < 500:
                logger.error("❌ UTP rejected the POST (HTTP %s, not retried) token=%s lead=%s body=%s",
                             r.status_code, token, lead, r.text[:2000])
                return None, f"rejected_{r.status_code}"
            outcome = f"http_{r.status_code}"
            logger.warning("⚠️  UTP POST HTTP %s token=%s lead=%s attempt=%d/%d body=%s",
                           r.status_code, token, lead, attempt, attempts, r.text[:2000])

        if attempt < attempts:
            time.sleep(UTP_RETRY_DELAYS[attempt - 1])

    return None, outcome


def write_sg_message_id(user_email: str, sg_message_id: str, token: str | None = None):
    """
    Called by reportgen right after sending the UTP student email.
    Stamps sg_message_id + initial email_status="Enviado" onto the job's row
    (by token; email only as fallback). Everything after this matches by
    sg_message_id.
    """
    from datetime import date

    try:
        matched = _update_tracking_row(
            "sg_message_id = %s, email_status = %s, email_status_updated_at = %s",
            (sg_message_id, "Enviado", date.today()), token, user_email,
        )
        if matched == "none":
            logger.warning("write_sg_message_id: no row found for token=%s email=%s", token, user_email)
        else:
            logger.info("✅ sg_message_id=%s written (by %s) token=%s lead=%s", sg_message_id, matched, token, user_email)
    except Exception:
        logger.exception("⚠️  Failed to write sg_message_id for token=%s email=%s (non-fatal)", token, user_email)


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
    from urllib.parse import urlparse
    from redis import Redis
    from app.queue import REDIS_URL

    # Own client with timeouts: a cron run should fail fast, not hang.
    u = urlparse(REDIS_URL)
    logger.info("📬 email flush: connecting to Redis %s:%s", u.hostname, u.port)
    r = Redis.from_url(REDIS_URL, socket_connect_timeout=10, socket_timeout=30)
    r.ping()
    logger.info("📬 email flush: Redis OK")
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
    logger.info("📬 email flush: %d events buffered, connecting to MySQL", len(raw))
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
