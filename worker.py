#!/usr/bin/env python3
"""
Worker entrypoint — corre en un proceso/servicio separado en Railway.
Levanta un worker de RQ que escucha la cola "reports".
"""
import logging
import os
from redis import Redis
from rq import Worker, Queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("reportgen.worker")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
QUEUES    = os.environ.get("RQ_QUEUES", "reports").split(",")


def main():
    conn = Redis.from_url(REDIS_URL)
    queues = [Queue(name, connection=conn) for name in QUEUES]

    logger.info("🚀 Worker starting | queues=%s | redis=%s", QUEUES, REDIS_URL)

    worker = Worker(queues, connection=conn)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
