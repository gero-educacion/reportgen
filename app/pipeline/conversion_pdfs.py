from pathlib import Path
from urllib.parse import urlsplit
import os
import time
import socket
import random
import logging
import requests

logger = logging.getLogger(__name__)

GOTENBERG_URL = os.environ.get("GOTENBERG_URL", "http://gotenberg:3000")
CONVERSION_TIMEOUT = int(os.environ.get("LO_TIMEOUT", 120))
CONVERSION_ATTEMPTS = int(os.environ.get("GOTENBERG_ATTEMPTS", 6))
BACKOFF_CAP = 30  # segundos

# Gotenberg has several replicas behind one private hostname. requests always
# connects to the FIRST address DNS returns, so every worker ended up on the
# same replica. With GOTENBERG_SPREAD=1 (default) each attempt resolves the
# hostname and picks one of its addresses at random. One address (or http/https
# with an IP already, or DNS failing) = same behavior as before.
GOTENBERG_SPREAD = os.environ.get("GOTENBERG_SPREAD", "1") == "1"
_U = urlsplit(GOTENBERG_URL)


def _gotenberg_base() -> str:
    """Base URL for one attempt: a random replica when the hostname resolves to several."""
    if not GOTENBERG_SPREAD or _U.scheme != "http" or not _U.hostname:
        return GOTENBERG_URL.rstrip("/")
    port = _U.port or 80
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(_U.hostname, port, type=socket.SOCK_STREAM)})
    except OSError:
        return GOTENBERG_URL.rstrip("/")
    if len(ips) <= 1:
        return GOTENBERG_URL.rstrip("/")
    ip = random.choice(ips)
    host = f"[{ip}]" if ":" in ip else ip  # IPv6 (Railway private network) needs brackets
    return f"http://{host}:{port}"


def _is_retryable(status_code: int) -> bool:
    # 429: cola interna de Gotenberg llena. 5xx: timeout de cola o LibreOffice caído.
    return status_code == 429 or status_code >= 500


def convert_to_pdf(pptx_path: Path, pdf_path: Path) -> Path:
    if not pptx_path.exists():
        raise FileNotFoundError(pptx_path)

    logger.info("🖨️  Sending to Gotenberg: %s", pptx_path.name)

    # Bajo carga Gotenberg a veces devuelve 429 (cola interna llena), 503 (la
    # cola superó el API timeout) o 500 (se cayó LibreOffice y lo reinicia).
    # Reintentar solo la conversión es mucho más barato que fallar el job entero.
    # Each attempt may land on a different replica, so a busy or restarting one
    # doesn't stall the job; connection errors are retried the same way.
    response = None
    for attempt in range(CONVERSION_ATTEMPTS):
        base = _gotenberg_base()
        last = attempt == CONVERSION_ATTEMPTS - 1
        try:
            with open(pptx_path, "rb") as f:
                response = requests.post(
                    f"{base}/forms/libreoffice/convert",
                    files={
                        "files": (
                            pptx_path.name,
                            f,
                            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        )
                    },
                    timeout=CONVERSION_TIMEOUT,
                )
        except requests.ConnectionError as e:
            if last:
                raise
            reason = f"connection error on {base} ({type(e).__name__})"
        else:
            if not _is_retryable(response.status_code) or last:
                break
            reason = f"HTTP {response.status_code} from {base}"
        # Backoff exponencial con jitter: que los workers no reintenten todos a la vez.
        delay = min(BACKOFF_CAP, 2 ** (attempt + 1)) * random.uniform(0.5, 1.0)
        logger.warning(
            "⚠️  Gotenberg %s for %s, retrying in %.1fs (%d/%d)",
            reason, pptx_path.name, delay, attempt + 1, CONVERSION_ATTEMPTS - 1,
        )
        time.sleep(delay)

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
