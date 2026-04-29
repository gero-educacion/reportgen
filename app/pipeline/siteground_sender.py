"""
siteground_sender.py
--------------------
Two responsibilities:

1. upload_pdf_to_siteground()
   Uploads a PDF to SiteGround via SFTP and returns its public URL.
   Used for clients (e.g. UTP) who need a non-Drive file URL.

2. send_report_to_siteground()
   Notifies the WordPress REST endpoint that a new report is available
   (existing behaviour — unchanged).

Required env vars for SFTP upload:
  SG_SFTP_HOST        — found in SiteGround dashboard → Dev Tools → SFTP & SSH
                        looks like: access715.runhosting.com
  SG_SFTP_PORT        — also in that panel, SiteGround uses a non-standard port
                        (NOT 22), typically something like 18765
  SG_SFTP_USER        — your cPanel / hosting username, e.g. staging2geroedu
  SG_SFTP_PASSWORD    — your cPanel password
  SG_SFTP_REMOTE_DIR  — absolute path on the server, e.g.:
                        /home/staging2geroedu/public_html/pdf_storage
  SG_PUBLIC_BASE_URL  — the public URL that maps to that folder, e.g.:
                        https://staging2.geroeducacion.com/pdf_storage

Required env vars for WP notification (existing):
  SITEGROUND_API_KEY
  SITEGROUND_ENDPOINT
"""

import os
import logging
import requests
import paramiko
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

REPORT_DESCRIPTIONS = {
    'Reporte "Autoconocimiento"': (
        "Tu Guía de Orientación Vocacional, incluye información sobre tu personalidad, "
        "intereses, habilidades e hipótesis de carrera que te recomendamos explorar."
    ),
    'Reporte "Autoconocimiento versión padres"': (
        "Los resultados obtenidos en autoconocimiento para que puedas compartirlo con tus padres. "
        "No te preocupes, la información es la misma que recibiste tú!"
    ),
    "CCR EN BOXES": "",
    "CCR CALENTANDO MOTORES": "",
    "CCR A TODA MARCHA": "",
}


# ---------------------------------------------------------------------------
# SFTP upload
# ---------------------------------------------------------------------------
import io

def _get_sftp_client():
    host        = os.environ["SG_SFTP_HOST"]
    port        = int(os.environ.get("SG_SFTP_PORT", 18765))
    user        = os.environ["SG_SFTP_USER"]
    private_key = os.environ["SG_SFTP_PRIVATE_KEY"]  # contents of siteground_key

    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=host, port=port, username=user, pkey=pkey)
    sftp = ssh.open_sftp()
    return ssh, sftp


def upload_pdf_to_siteground(local_path: Path, filename: str) -> str:
    """
    Uploads local_path to SG_SFTP_REMOTE_DIR/<filename> via SFTP.
    Returns the public URL: SG_PUBLIC_BASE_URL/<filename>
    Runs once per student — the returned URL is permanent.
    """
    remote_dir  = os.environ["SG_SFTP_REMOTE_DIR"]
    public_base = os.environ["SG_PUBLIC_BASE_URL"].rstrip("/")
    remote_path = str(PurePosixPath(remote_dir) / filename)

    logger.info("📤 SFTP upload: %s → %s", filename, remote_path)

    ssh, sftp = _get_sftp_client()
    try:
        sftp.put(str(local_path), remote_path)
        sftp.chmod(remote_path, 0o644)   # world-readable
    finally:
        sftp.close()
        ssh.close()

    public_url = f"{public_base}/{filename}"
    logger.info("✅ SiteGround file live at: %s", public_url)
    return public_url


# ---------------------------------------------------------------------------
# WP REST notification (existing — unchanged)
# ---------------------------------------------------------------------------

def send_report_to_siteground(
    email: str,
    drive_link: str,
    post_title: str,
):
    logger.info("🌐 Sending to SiteGround | email=%s | title=%s", email, post_title)

    api_key  = os.environ.get("SITEGROUND_API_KEY")
    endpoint = os.environ.get("SITEGROUND_ENDPOINT")

    if not api_key or not endpoint:
        raise RuntimeError("SiteGround env vars not configured")

    endpoint = endpoint.strip().strip('"')

    descripcion_reporte = REPORT_DESCRIPTIONS.get(post_title, "")

    payload = {
        "post_title":          post_title,
        "tipo_reporte":        post_title,
        "descripcion_reporte": descripcion_reporte,
        "drive_link":          drive_link,
        "the_email":           email,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept":           "application/json, text/plain, */*",
        "Content-Type":     "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    logger.info("📨 POST /reportes/v1/upload")
    logger.info("Payload: %s", payload)

    r = requests.post(
        endpoint,
        json=payload,
        headers=headers,
        timeout=20,
        allow_redirects=True,
    )

    logger.info("Status code: %s", r.status_code)
    logger.info("Response headers: %s", dict(r.headers))
    logger.info("Response body: %s", r.text[:500])

    content_type = r.headers.get("Content-Type", "")
    if "text/html" in content_type.lower():
        logger.error("🚨 SiteGround returned HTML (likely CAPTCHA/WAF)")
        raise RuntimeError("SiteGround CAPTCHA / WAF triggered")

    r.raise_for_status()

    logger.info("✅ SiteGround accepted payload")
    return r.json()