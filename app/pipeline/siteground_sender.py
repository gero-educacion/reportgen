import os
import requests
import logging

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


def send_report_to_siteground(
    email: str,
    drive_link: str,
    post_title: str,
):
    logger.info(
        f"🌐 Sending to SiteGround | email={email} | title={post_title}"
    )

    api_key = os.environ.get("SITEGROUND_API_KEY")
    endpoint = os.environ.get("SITEGROUND_ENDPOINT")

    if not api_key or not endpoint:
        raise RuntimeError("SiteGround env vars not configured")

    endpoint = endpoint.strip().strip('"')

    descripcion_reporte = REPORT_DESCRIPTIONS.get(post_title, "")

    payload = {
        "post_title": post_title,
        "tipo_reporte": post_title,
        "descripcion_reporte": descripcion_reporte,
        "drive_link": drive_link,
        "the_email": email,
    }

    headers = {
        # 🔑 Auth
        "Authorization": f"Bearer {api_key}",

        # 🧠 Make this look like a boring browser POST
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",

        # Optional but harmless
        "X-Requested-With": "XMLHttpRequest",
    }

    logger.info("📨 POST /reportes/v1/upload")
    logger.info(f"Payload: {payload}")

    r = requests.post(
        endpoint,
        json=payload,
        headers=headers,
        timeout=20,
        allow_redirects=True,  # 🔴 IMPORTANT
    )

    logger.info(f"Status code: {r.status_code}")
    logger.info(f"Response headers: {dict(r.headers)}")
    logger.info(f"Response body: {r.text[:500]}")

    # 🚨 Early detection of captcha / HTML
    content_type = r.headers.get("Content-Type", "")
    if "text/html" in content_type.lower():
        logger.error("🚨 SiteGround returned HTML (likely CAPTCHA/WAF)")
        raise RuntimeError("SiteGround CAPTCHA / WAF triggered")

    r.raise_for_status()

    logger.info("✅ SiteGround accepted payload")
    return r.json()
