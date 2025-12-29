import os
import requests
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

REPORT_DESCRIPTIONS = {
    'Reporte "Autoconocimiento"': "Tu Guía de Orientación Vocacional, incluye información sobre tu personalidad, intereses, habilidades e hipótesis de carrera que te recomendamos explorar.",
    'Reporte "Autoconocimiento versión padres"': "Los resultados obtenidos en autoconocimiento para que puedas compartirlo con tus padres. No te preocupes, la información es la misma que recibiste tú!",
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

    descripcion_reporte = REPORT_DESCRIPTIONS.get(post_title, "")

    payload = {
        "post_title": post_title,
        "tipo_reporte": post_title,
        "descripcion_reporte": descripcion_reporte,
        "drive_link": drive_link,
        "the_email": email,
    }

    logger.info(f"The payload consists of: post_title -> {post_title}, tipo_reporte -> {post_title}, descripcion_reporte -> {descripcion_reporte}, drive_link -> {drive_link}, the_email -> {email}")

    headers = {
        "Authorization": f"Bearer {os.environ['SITEGROUND_API_KEY']}",
    }

    endpoint = os.environ["SITEGROUND_ENDPOINT"].strip().strip('"')

    r = requests.post(
        endpoint,
        json=payload,
        headers=headers,
        timeout=20,
    )

    logger.info("📨 POST /reportes/v1/upload")
    logger.info(f"Payload: {payload}")
    logger.info(f"Status code: {r.status_code}")
    logger.info(f"Response body: {r.text}")
    

    r.raise_for_status()
    logger.info(f"✅ SiteGround response: {r}")
    return r.json()
