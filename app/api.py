from fastapi import FastAPI, HTTPException, Request
from datetime import datetime, timezone
from pathlib import Path
import logging
import json
import os
from sendgrid.helpers.eventwebhook import EventWebhook

from app.queue import enqueue_report, get_job_status
from app.pipeline.job_config import role_is_ready

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("reportgen")

app = FastAPI()

logger.info("ok, let's go (queue mode)")


@app.get("/health")
def health():
    from app.queue import get_redis
    try:
        get_redis().ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    return {
        "pipeline_exists": Path("/app/app/pipeline").exists(),
        "assets_exists":   Path("/app/assets").exists(),
        "tmp_exists":      Path("/app/tmp").exists(),
        "data_exists":     Path("/app/data").exists(),
        "redis_ok":        redis_ok,
    }


@app.post("/run")
def run_student(job: dict):
    student_id = job.get("student_id")
    job_id     = job.get("job_id") or student_id
    rol = job.get("Rol") or ""

    if not student_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing student_id or job_id")

    ready, problems = role_is_ready(rol)
    if not ready:
        raise HTTPException(status_code=422, detail=f"Role '{rol}' not ready: {problems}")

    try:
        rq_job_id = enqueue_report(job)
    except Exception as e:
        logger.exception("💥 Failed to enqueue job %s", job_id)
        raise HTTPException(status_code=500, detail=f"Queue error: {e}")

    return {
        "status": "queued",
        "job_id": rq_job_id,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/status/{job_id}")
def job_status(job_id: str):
    return get_job_status(job_id)



SENDGRID_WEBHOOK_PUBLIC_KEY = os.environ.get("SENDGRID_WEBHOOK_PUBLIC_KEY", "")

@app.post("/webhooks/sendgrid")
async def sendgrid_webhook(request: Request):
    from app.pipeline.db_writer import (
        update_email_status_by_sg_message_id,
        BOUNCE_TRIGGER_EVENTS,
        MAX_RESEND_ATTEMPTS,
    )
    from app.pipeline.email_sender import send_utp_student_email

    body = await request.body()

    if SENDGRID_WEBHOOK_PUBLIC_KEY:
        signature = request.headers.get("X-Twilio-Email-Event-Webhook-Signature", "")
        timestamp = request.headers.get("X-Twilio-Email-Event-Webhook-Timestamp", "")
        ew = EventWebhook()
        public_key = ew.convert_public_key_to_ecdsa(SENDGRID_WEBHOOK_PUBLIC_KEY)
        if not ew.verify_signature(body.decode("utf-8"), signature, timestamp, public_key):
            logger.warning("SendGrid webhook: signature verification failed")
            raise HTTPException(status_code=401, detail="Invalid signature")

    events = json.loads(body)
    processed = 0

    for e in events:
        sg_message_id = e.get("sg_message_id", "")
        event_type = e.get("event")
        if not sg_message_id or not event_type:
            continue

        row = update_email_status_by_sg_message_id(sg_message_id, event_type)
        if row is None:
            continue
        processed += 1

        if event_type in BOUNCE_TRIGGER_EVENTS:
            cedula = row["email"] 
            resend_count = row["resend_count"] or 0
            reporte_url = row["reporte_estudiante"]

            if resend_count >= MAX_RESEND_ATTEMPTS:
                logger.warning("cedula=%s hit max resend attempts (%s), leaving for manual review", cedula, resend_count)
            elif not reporte_url:
                logger.warning("cedula=%s bounced but no reporte_estudiante URL stored, cannot resend", cedula)
            else:
                logger.info("🔁 Resending for cedula=%s (attempt %s/%s)", cedula, resend_count + 1, MAX_RESEND_ATTEMPTS)
                try:
                    send_utp_student_email(cedula=cedula, reporte_url=reporte_url, is_resend=True)
                except Exception:
                    logger.exception("Resend failed for cedula=%s", cedula)

    return {"received": len(events), "processed": processed}
