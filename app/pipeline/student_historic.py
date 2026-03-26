"""
student_historic.py
-------------------
Persistent source of truth for student report history.
Lives on a Railway-mounted volume at /app/data/students_historic.json.

Schema:
{
  "a012345@tec.mx": {
    "student_id": "a012345",
    "name": "Juan Pérez",
    "email": "a012345@tec.mx",
    "runs": [
      {
        "date": "2025-03-26T14:32:00",
        "links": {
          "estudiante": "https://drive.google.com/...",
          "padres":     "https://drive.google.com/..."
        }
      },
      {
        "date": "2025-06-01T09:10:00",
        "links": {
          "ccr_rojo": "https://drive.google.com/..."
        }
      }
    ],
    "all_links": {
      "estudiante": "https://drive.google.com/...",   ← latest link per type
      "padres":     "https://drive.google.com/...",
      "ccr_rojo":   "https://drive.google.com/..."
    }
  }
}

`all_links` is a flattened view of every report type this student has ever had,
always pointing to the most recent link for that type. It's the fast lookup for
the fallback mechanic.
"""

import json
import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORIC_PATH = Path(os.environ.get("HISTORIC_PATH", "/app/data/students_historic.json"))
LOCK_PATH     = HISTORIC_PATH.with_suffix(".lock")
LOCK_TIMEOUT  = 15   # seconds


# ---------------------------------------------------------------------------
# Internal: file-level locking (same atomic pattern as job_dir locks)
# ---------------------------------------------------------------------------

def _acquire_file_lock(timeout: int = LOCK_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.2)
    return False


def _release_file_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _load_raw() -> dict:
    if not HISTORIC_PATH.exists():
        return {}
    try:
        return json.loads(HISTORIC_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("⚠️  Could not parse students_historic.json — starting fresh")
        return {}


def _save_raw(data: dict):
    HISTORIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORIC_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(HISTORIC_PATH)   # atomic on POSIX


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_student(email: str) -> dict | None:
    """Return the student record for this email, or None if not found."""
    data = _load_raw()
    return data.get(email)


def upsert_student(
    email: str,
    student_id: str,
    name: str,
    drive_links: dict,          # {report_type: drive_link_url}
):
    """
    Merge a new run's drive links into this student's record.
    Creates the record if it doesn't exist.
    Grouped by run date, with a flat all_links view for quick lookup.
    Thread/process-safe via file lock.
    """
    if not email:
        logger.warning("upsert_student: no email provided, skipping")
        return

    if not _acquire_file_lock():
        logger.error("Could not acquire historic lock — skipping upsert for %s", email)
        return

    try:
        data   = _load_raw()
        record = data.get(email, {
            "student_id": student_id,
            "name":       name,
            "email":      email,
            "runs":       [],
            "all_links":  {},
        })

        # Always keep name/id fresh
        record["student_id"] = student_id
        record["name"]       = name

        # Append this run
        run_entry = {
            "date":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "links": drive_links,
        }
        record["runs"].append(run_entry)

        # Merge into flat all_links (newer run overwrites older for same type)
        record["all_links"].update(drive_links)

        data[email] = record
        _save_raw(data)
        logger.info("✅ Historic updated for %s | new_links=%s", email, list(drive_links.keys()))

    finally:
        _release_file_lock()


def get_all_links(email: str) -> dict:
    """
    Returns the flat {report_type: drive_url} dict for this student,
    or {} if they have no history.
    """
    record = get_student(email)
    if not record:
        return {}
    return record.get("all_links", {})