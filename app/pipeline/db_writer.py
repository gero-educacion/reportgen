"""
db_writer.py
------------
1. write_majors_to_db()  — upserts 4 majors for any student into byw_autoconocimiento
2. post_utp_payload()    — looks up legajo from byw_usuarios_habilitados by email,
                           then sends it as leadId to the UTP CRM endpoint

UTP endpoint payload shape:
  {
    "leadId":      "<legajo from byw_usuarios_habilitados>",
    "career1":     "<Carrera 1>",
    "career2":     "<Carrera 2>",
    "resultsLink": "<SiteGround public URL of the report>"
  }

Required env vars (MySQL):
  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

Required env vars (UTP endpoint):
  UTP_ENDPOINT_URL
  UTP_API_KEY  (optional)
"""

import os
import logging
import requests
import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------

def _get_connection() -> pymysql.Connection:
    return pymysql.connect(
        host        = os.environ["DB_HOST"],
        port        = int(os.environ.get("DB_PORT", 3306)),
        user        = os.environ["DB_USER"],
        password    = os.environ["DB_PASSWORD"],
        database    = os.environ["DB_NAME"],
        charset     = "utf8mb4",
        cursorclass = pymysql.cursors.DictCursor,
        connect_timeout = 10,
    )


def _get_legajo(email: str) -> str | None:
    """
    Looks up the legajo for this student in byw_usuarios_habilitados by email.
    Returns the legajo string or None if not found.
    """
    sql = """
        SELECT legajo
        FROM byw_usuarios_habilitados
        WHERE LOWER(email) = LOWER(%s)
        LIMIT 1
    """
    try:
        conn = _get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (email,))
                row = cur.fetchone()
        if row:
            logger.info("✅ Found legajo=%s for %s", row["legajo"], email)
            return str(row["legajo"])
        else:
            logger.warning("⚠️  No legajo found for email=%s", email)
            return None
    except Exception:
        logger.exception("⚠️  Failed to look up legajo for %s", email)
        return None


def write_majors_to_db(student: dict):
    """
    Upserts the student's top 4 majors into byw_autoconocimiento.
    Matches the WP user by email (case-insensitive).
    """
    email = (student.get("Email") or student.get("email") or "").strip()
    if not email:
        logger.warning("write_majors_to_db: no email, skipping")
        return

    c1 = student.get("Carrera 1", "")
    c2 = student.get("Carrera 2", "")
    c3 = student.get("Carrera 3", "")
    c4 = student.get("Carrera 4", "")

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
    except Exception:
        logger.exception("⚠️  Failed to write majors to DB for %s (non-fatal)", email)


# ---------------------------------------------------------------------------
# UTP endpoint
# ---------------------------------------------------------------------------

def post_utp_payload(
    student_id: str,
    student: dict,
    report_links: dict,         # {report_type: public_url}
):
    """
    Looks up legajo from DB by email, then POSTs to UTP CRM endpoint.
    Falls back to student_id as leadId if legajo not found.
    """
    endpoint = os.environ.get("UTP_ENDPOINT_URL")
    if not endpoint:
        logger.warning("UTP_ENDPOINT_URL not set — skipping")
        return

    email   = (student.get("Email") or student.get("email") or "").strip()
    api_key = os.environ.get("UTP_API_KEY", "")

    # Look up legajo — fall back to student_id if not found
    legajo = _get_legajo(email) if email else None
    if not legajo:
        logger.warning("⚠️  No legajo found, falling back to student_id=%s", student_id)
        legajo = student_id

    results_link = (
        report_links.get("estudiante")
        or report_links.get("padres")
        or next(iter(report_links.values()), "")
    )

    payload = {
        "leadId":      legajo,
        "career1":     student.get("Carrera 1", ""),
        "career2":     student.get("Carrera 2", ""),
        "resultsLink": results_link,
    }

    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info("📨 Posting UTP payload | leadId=%s | link=%s", legajo, results_link)

    try:
        r = requests.post(endpoint, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        body = r.json()
        logger.info("✅ UTP endpoint response: %s", body)
        if not body.get("success"):
            logger.warning("⚠️  UTP endpoint returned success=false: %s", body)
    except Exception:
        logger.exception("⚠️  UTP endpoint post failed (non-fatal)")