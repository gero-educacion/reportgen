import os
import logging
from redis import Redis
from rq import Queue, Retry
from rq.job import Job

logger = logging.getLogger("reportgen.queue")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

def get_redis() -> Redis:
    return Redis.from_url(REDIS_URL)

def get_queue(name: str = "reports") -> Queue:
    return Queue(name, connection=get_redis(), default_timeout=600)

def enqueue_report(job: dict) -> str:
    """
    Encola un job de generación de reporte.
    Devuelve el rq job_id (string).
    """
    from app.tasks import process_report_job

    q = get_queue()
    rq_job = q.enqueue(
        process_report_job,
        job,
        job_id=job.get("job_id") or job.get("student_id"),
        retry=Retry(max=3, interval=60),
        meta={"student_id": job.get("student_id"), "email": job.get("Email") or job.get("email")},
    )
    logger.info("📥 Job enqueued: %s", rq_job.id)
    logger.info("found me yet?")
    return rq_job.id

def get_job_status(job_id: str) -> dict:
    """Devuelve el estado de un job en la cola."""
    try:
        rq_job = Job.fetch(job_id, connection=get_redis())
        return {
            "job_id":   job_id,
            "status":   rq_job.get_status().value,
            "enqueued": rq_job.enqueued_at.isoformat() if rq_job.enqueued_at else None,
            "started":  rq_job.started_at.isoformat()  if rq_job.started_at  else None,
            "ended":    rq_job.ended_at.isoformat()     if rq_job.ended_at    else None,
            "exc":      rq_job.exc_info if rq_job.is_failed else None,
        }
    except Exception as e:
        return {"job_id": job_id, "status": "not_found", "error": str(e)}
