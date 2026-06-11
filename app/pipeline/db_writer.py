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
import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------

def _get_connection() -> pymysql.Connection:
    return pymysql.connect(
        host            = os.environ["DB_HOST"],
        port            = int(os.environ.get("DB_PORT", 3306)),
        user            = os.environ["DB_USER"],
        password        = os.environ["DB_PASSWORD"],
        database        = os.environ["DB_NAME"],
        charset         = "utf8mb4",
        cursorclass     = pymysql.cursors.DictCursor,
        connect_timeout = 10,
    )


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
 
    payload = {
        "leadId":      user_email,
        "career1":     c1,
        "career2":     "",
        "resultsLink":       report_links.get("estudiante", ""),
        "resultsLinkParent": report_links.get("padres", ""),
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
