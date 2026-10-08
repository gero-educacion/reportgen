from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
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
_SG_PUBLIC_KEY = (
    EventWebhook().convert_public_key_to_ecdsa(SENDGRID_WEBHOOK_PUBLIC_KEY)
    if SENDGRID_WEBHOOK_PUBLIC_KEY else None
)


class _SkipWebhookAccessLog(logging.Filter):
    def filter(self, record):
        return "/webhooks/sendgrid" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(_SkipWebhookAccessLog())


@app.post("/webhooks/sendgrid")
async def sendgrid_webhook(request: Request):
    """
    Only buffers events in Redis (one pipelined RPUSH) and returns. The DB
    is updated in batch twice a day by app/flush_email_status.py — doing a
    DB round trip per event here was flooding reportgen.
    """
    from app.pipeline.db_writer import buffer_email_events

    body = await request.body()

    if _SG_PUBLIC_KEY:
        signature = request.headers.get("X-Twilio-Email-Event-Webhook-Signature", "")
        timestamp = request.headers.get("X-Twilio-Email-Event-Webhook-Timestamp", "")
        if not EventWebhook().verify_signature(body.decode("utf-8"), signature, timestamp, _SG_PUBLIC_KEY):
            logger.warning("SendGrid webhook: signature verification failed")
            raise HTTPException(status_code=401, detail="Invalid signature")

    events = json.loads(body)
    buffered = await run_in_threadpool(buffer_email_events, events)
    return {"received": len(events), "buffered": buffered}
