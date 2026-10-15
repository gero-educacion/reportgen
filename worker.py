#!/usr/bin/env python3
"""
Worker entrypoint — corre en un proceso/servicio separado en Railway.

RQ_QUEUES   colas que escucha, en orden de prioridad (coma-separadas).
            Por default escucha todas: las livianas primero, y "reports"
            (la cola vieja) al final para drenar lo que quedó encolado ahí.
            Para workers dedicados, ej: RQ_QUEUES=reports_full
RQ_WORKERS  cantidad de procesos worker en este contenedor (default 1).
"""
import logging
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


def main():
    conn = Redis.from_url(REDIS_URL)
    queues = [Queue(name.strip(), connection=conn) for name in QUEUES]

    logger.info("🚀 Worker starting | queues=%s | workers=%d", QUEUES, NUM_WORKERS)

    if NUM_WORKERS > 1:
        WorkerPool(queues, connection=conn, num_workers=NUM_WORKERS).start()
    else:
        Worker(queues, connection=conn).work(with_scheduler=True)


if __name__ == "__main__":
    main()
