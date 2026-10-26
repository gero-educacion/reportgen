from pathlib import Path
import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

GOTENBERG_URL = os.environ.get("GOTENBERG_URL", "http://gotenberg:3000")
CONVERSION_TIMEOUT = int(os.environ.get("LO_TIMEOUT", 120))
CONVERSION_ATTEMPTS = 3


def convert_to_pdf(pptx_path: Path, pdf_path: Path) -> Path:
    if not pptx_path.exists():
        raise FileNotFoundError(pptx_path)

    logger.info("🖨️  Sending to Gotenberg: %s", pptx_path.name)

    # Bajo carga Gotenberg a veces devuelve 503 (su cola interna superó el
    # API timeout) o 500 (se cayó LibreOffice y lo reinicia). Reintentar solo
    # la conversión es mucho más barato que fallar el job entero.
    for attempt in range(CONVERSION_ATTEMPTS):
        with open(pptx_path, "rb") as f:
            response = requests.post(
                f"{GOTENBERG_URL}/forms/libreoffice/convert",
                files={
                    "files": (
                        pptx_path.name,
                        f,
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    )
                },
                timeout=CONVERSION_TIMEOUT,
            )
        if response.status_code < 500 or attempt == CONVERSION_ATTEMPTS - 1:
            break
        logger.warning(
            "⚠️  Gotenberg HTTP %s for %s, retrying (%d/%d)",
            response.status_code, pptx_path.name, attempt + 1, CONVERSION_ATTEMPTS - 1,
        )
        time.sleep(2 * (attempt + 1))

    if response.status_code != 200:
        raise RuntimeError(
            f"Gotenberg conversion failed (HTTP {response.status_code}): {response.text[:300]}"
        )

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(response.content)

    if pdf_path.stat().st_size < 1000:
        raise RuntimeError(f"Gotenberg returned a suspiciously small PDF for {pptx_path.name}")

    logger.info("✅ PDF received from Gotenberg: %s (%d bytes)", pdf_path.name, pdf_path.stat().st_size)

    return pdf_path