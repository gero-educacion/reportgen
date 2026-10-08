#!/usr/bin/env python3
"""
Entrypoint del servicio cron "email-flush" en Railway (ver railway.email-flush.toml).
Vacía el buffer de eventos del webhook de SendGrid (Redis), actualiza
email_status en byw_tracking_algoritmo_AC en batch y termina. No corre
dentro de reportgen ni del worker, así no les quita recursos.

    python -m app.flush_email_status
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("reportgen.email_flush")


def main() -> int:
    from app.pipeline.db_writer import flush_email_status_buffer

    try:
        flush_email_status_buffer()
    except Exception:
        logger.exception("⚠️  UTP email status flush failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
