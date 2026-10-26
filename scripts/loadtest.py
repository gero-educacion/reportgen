#!/usr/bin/env python3
"""
Load test de reportgen contra un entorno de STAGING.

Encola N jobs falsos en la cola "loadtest" y mide throughput real mientras
los workers de staging (con LOADTEST_MODE=1) los procesan con Gotenberg real.

    pip install redis rq
    python scripts/loadtest.py run   --redis-url <REDIS staging> -n 300 --rol UTP
    python scripts/loadtest.py watch --redis-url <REDIS staging>       # re-engancharse a una corrida
    python scripts/loadtest.py clean --redis-url <REDIS staging>       # borrar cola y registros

Los payloads salen de mock_payloads.txt (uno por rol, un JSON por línea);
a cada copia se le cambia student_id/job_id/email.

Se niega a correr si --redis-url es el REDIS_URL del .env (prod), salvo --i-know-this-is-prod.
"""
import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

from redis import Redis
from rq import Queue
from rq.job import Job
from rq.registry import FailedJobRegistry, FinishedJobRegistry, StartedJobRegistry

ROOT = Path(__file__).resolve().parent.parent
LOADTEST_QUEUE = "loadtest"  # = app.loadtest.LOADTEST_QUEUE (no se importa para no traer app/ entero)


def prod_redis_url() -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("REDIS_URL="):
            return line.split("=", 1)[1].strip().strip('"\'')
    return None


def load_sample(path: Path, rol: str) -> dict:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line)
            if payload.get("Rol") == rol:
                return payload
    sys.exit(f"No hay payload de ejemplo para Rol={rol!r} en {path}")


def enqueue(q: Queue, sample: dict, n: int, run_id: str):
    for i in range(n):
        job_id = f"lt-{run_id}-{i:05d}"
        job = dict(sample)
        job.update({
            "student_id": job_id,
            "job_id": job_id,
            "Email": f"{job_id}@loadtest.invalid",
            "user_email": job_id,
            "force_rerun": True,   # saltear el atajo de histórico
        })
        q.enqueue(
            "app.tasks.process_report_job", job,
            job_id=job_id,
            job_timeout=600,
            result_ttl=6 * 3600,   # que FinishedJobRegistry los guarde para medir
            failure_ttl=6 * 3600,
        )
    print(f"{n} jobs encolados en '{LOADTEST_QUEUE}' (run {run_id})")


def durations(conn: Redis, ids: list[str]) -> tuple[list[float], list[float]]:
    run, wait = [], []
    for i in range(0, len(ids), 200):
        for job in Job.fetch_many(ids[i:i + 200], connection=conn):
            if job and job.started_at and job.ended_at:
                run.append((job.ended_at - job.started_at).total_seconds())
                if job.enqueued_at:
                    wait.append((job.started_at - job.enqueued_at).total_seconds())
    return run, wait


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def watch(conn: Redis, q: Queue, interval: int):
    finished = FinishedJobRegistry(queue=q)
    started = StartedJobRegistry(queue=q)
    failed = FailedJobRegistry(queue=q)

    t0 = time.time()
    done0 = finished.count + failed.count
    last_t, last_done = t0, done0
    print(f"{'t':>6} {'cola':>6} {'corriendo':>9} {'ok':>6} {'fallados':>8} {'jobs/min':>9}")
    while True:
        queued, running = q.count, started.count
        ok, bad = finished.count, failed.count
        now = time.time()
        rate = (ok + bad - last_done) / max(now - last_t, 1e-9) * 60
        print(f"{int(now - t0):>5}s {queued:>6} {running:>9} {ok:>6} {bad:>8} {rate:>9.1f}")
        last_t, last_done = now, ok + bad
        if queued == 0 and running == 0:
            break
        time.sleep(interval)

    elapsed = time.time() - t0
    total = finished.count + failed.count - done0
    run, wait = durations(conn, finished.get_job_ids())
    print("\n=== RESULTADO ===")
    print(f"jobs terminados: {finished.count} ok, {failed.count} fallados")
    if total and elapsed > 0:
        print(f"throughput:      {total / elapsed * 60:.1f} jobs/min ({elapsed / 60:.1f} min)")
    if run:
        print(f"duracion job:    p50 {pct(run, 50):.1f}s | p95 {pct(run, 95):.1f}s | max {max(run):.1f}s")
        print(f"espera en cola:  p50 {pct(wait, 50):.0f}s | p95 {pct(wait, 95):.0f}s")
    workers = [w for w in q.connection.smembers("rq:workers")]
    print(f"workers vivos:   {len(workers)}")
    if failed.count:
        sample = Job.fetch(failed.get_job_ids()[0], connection=conn)
        print("\nprimer error:\n" + (sample.exc_info or "")[-1500:])


def clean(conn: Redis, q: Queue):
    for reg in (FinishedJobRegistry(queue=q), FailedJobRegistry(queue=q)):
        for job_id in reg.get_job_ids():
            reg.remove(job_id, delete_job=True)
    q.empty()
    print(f"cola '{LOADTEST_QUEUE}' y registros limpiados")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "watch", "clean"])
    ap.add_argument("--redis-url", required=True, help="REDIS_URL público del entorno de staging")
    ap.add_argument("-n", type=int, default=200, help="cantidad de jobs (run)")
    ap.add_argument("--rol", default="UTP", help="Rol del payload de ejemplo (run)")
    ap.add_argument("--payloads", default=str(ROOT / "mock_payloads.txt"))
    ap.add_argument("--interval", type=int, default=30, help="segundos entre lecturas")
    ap.add_argument("--i-know-this-is-prod", action="store_true")
    args = ap.parse_args()

    if args.redis_url == prod_redis_url() and not args.i_know_this_is_prod:
        sys.exit("ERROR: Ese es el REDIS_URL de prod (.env). Usá el de staging.")

    conn = Redis.from_url(args.redis_url)
    conn.ping()
    q = Queue(LOADTEST_QUEUE, connection=conn)

    if args.cmd == "clean":
        clean(conn, q)
        return
    if args.cmd == "run":
        enqueue(q, load_sample(Path(args.payloads), args.rol), args.n, uuid.uuid4().hex[:6])
    watch(conn, q, args.interval)


if __name__ == "__main__":
    main()
