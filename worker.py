#!/usr/bin/env python3
"""
Worker entrypoint — corre en un proceso/servicio separado en Railway.

RQ_QUEUES   colas que escucha, en orden de prioridad (coma-separadas).
            Por default escucha todas: las livianas primero, y "reports"
            (la cola vieja) al final para drenar lo que quedó encolado ahí.
            Para workers dedicados, ej: RQ_QUEUES=reports_full
RQ_WORKERS  cantidad de procesos worker en este contenedor (default 1).
LOADTEST_MODE=1  load test: escucha SOLO la cola "loadtest", efectos externos
            mockeados y sin flush a la Sheet (ver app/loadtest.py).

Además levanta un proceso aparte que vuelca a la Google Sheet de status las
filas que los jobs dejan en Redis (ver sheets_updater.run_status_flush_loop).
"""
import logging
import multiprocessing
import os
from redis import Redis
from rq import Worker, Queue
from rq.worker_pool import WorkerPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("reportgen.worker")

REDIS_URL   = os.environ.get("REDIS_URL", "redis://localhost:6379")
QUEUES      = os.environ.get(
    "RQ_QUEUES", "reports_chill,reports_utp,reports_full,reports"
).split(",")
NUM_WORKERS = int(os.environ.get("RQ_WORKERS", "1"))
LOADTEST    = os.environ.get("LOADTEST_MODE") == "1"


def main():
    if LOADTEST:
        from app.loadtest import LOADTEST_QUEUE
        import app.tasks  # noqa: F401 — instala los mocks antes de arrancar
        queue_names = [LOADTEST_QUEUE]  # nunca las colas reales, aunque RQ_QUEUES diga otra cosa
    else:
        from app.pipeline.sheets_updater import run_status_flush_loop

        # Antes de crear cualquier conexión: el proceso hijo arranca limpio.
        multiprocessing.Process(target=run_status_flush_loop, name="status-flush", daemon=True).start()
        queue_names = QUEUES

    conn = Redis.from_url(REDIS_URL)
    queues = [Queue(name.strip(), connection=conn) for name in queue_names]

    logger.info("🚀 Worker starting | queues=%s | workers=%d", queue_names, NUM_WORKERS)

    if NUM_WORKERS > 1:
        WorkerPool(queues, connection=conn, num_workers=NUM_WORKERS).start()
    else:
        Worker(queues, connection=conn).work(with_scheduler=True)


if __name__ == "__main__":
    main()
