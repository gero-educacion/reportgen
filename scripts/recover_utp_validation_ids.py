#!/usr/bin/env python3
"""
recover_utp_validation_ids.py
-----------------------------
One-off recovery (2026-11-19): re-POST to UTP's CRM endpoint the results of
students whose report was generated and emailed, but whose post_utp_payload
call failed, so byw_tracking_algoritmo_AC.validationId stayed NULL.

Nothing is regenerated and no email is sent: the payload is rebuilt from what
is already stored (links in reporte_estudiante / reporte_padres, careers and
contact data in historico), exactly as app/pipeline/db_writer.post_utp_payload
builds it.

Which rows
  client = 'UTP', validationId IS NULL, reporte_estudiante filled, processed
  since --since, and the LEAD has no validationId on any other row (UTP
  already confirmed it; their endpoint may reject a lead it has seen).
  One POST per lead: its most recent row with links.

What it writes
  validationId on THAT row, matched by token (not "the lead's latest row").
  Every attempt goes to scripts/outputs/recover_utp_<timestamp>.csv with the
  HTTP status and UTP's response body, so rejections can be shown to UTP.

Safety
  Dry run by default (prints what it would send). --send to actually POST.
  Paced (--delay), and stops after --max-fail consecutive failures, so if UTP
  rejects these leads it stops after a handful instead of hammering them.

Env (same as reportgen): DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME
                         UTP_ENDPOINT_URL UTP_API_KEY
Run from the reportgen root, e.g. with Railway's env:
  railway run python scripts/recover_utp_validation_ids.py                 # dry run
  railway run python scripts/recover_utp_validation_ids.py --send --limit 5   # small test first
  railway run python scripts/recover_utp_validation_ids.py --send
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:  # local runs: pick up reportgen/.env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.pipeline.db import db_cursor  # noqa: E402

TABLE = "byw_tracking_algoritmo_AC"


def fetch_candidates(since: str, lead: str | None) -> tuple[list[dict], int]:
    """Returns (one row per lead to re-POST, number of NULL rows without links)."""
    where_lead = "AND t.email = %s" if lead else ""
    params = [since] + ([lead] if lead else [])
    with db_cursor() as cur:
        cur.execute(f"""
            SELECT t.token, t.email AS lead_id, t.carrera_1,
                   t.reporte_estudiante, t.reporte_padres, t.historico, t.processed_at
            FROM {TABLE} t
            WHERE t.client = 'UTP'
              AND t.validationId IS NULL
              AND NULLIF(t.reporte_estudiante, '') IS NOT NULL
              AND t.processed_at >= %s
              {where_lead}
              AND NOT EXISTS (
                  SELECT 1 FROM {TABLE} v
                  WHERE v.client = 'UTP' AND v.email = t.email AND v.validationId IS NOT NULL
              )
            ORDER BY t.processed_at ASC, t.token ASC
        """, params)
        rows = cur.fetchall()

        cur.execute(f"""
            SELECT COUNT(*) AS n FROM {TABLE}
            WHERE client = 'UTP' AND validationId IS NULL
              AND NULLIF(reporte_estudiante, '') IS NULL
              AND processed_at >= %s
        """, [since])
        sin_links = cur.fetchone()["n"]

    latest = {}  # lead_id -> its most recent row with links
    for r in rows:
        latest[r["lead_id"]] = r
    return sorted(latest.values(), key=lambda r: r["processed_at"]), sin_links


def build_payload(row: dict) -> dict:
    """Same shape as db_writer.post_utp_payload."""
    try:
        job = json.loads(row["historico"] or "{}")
    except (TypeError, ValueError):
        job = {}
    c1 = job.get("CARRERA_01") or job.get("Carrera 01") or (row["carrera_1"] or "").upper()
    return {
        "leadId":            row["lead_id"],
        "career1":           c1,
        "career2":           "",
        "resultsLink":       row["reporte_estudiante"] or "",
        "resultsLinkParent": row["reporte_padres"] or "",
        "telephone":         job.get("Telefono") or "",
        "email":             job.get("Email_formulario") or "",
    }


def write_validation_id(token: str, validation_id: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            f"UPDATE {TABLE} SET validationId = %s WHERE token = %s AND client = 'UTP'",
            (validation_id, token),
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true", help="actually POST (default: dry run)")
    ap.add_argument("--since", default="2026-11-19 00:00:00", help="processed_at from (UTC, as stored)")
    ap.add_argument("--lead", help="only this lead_id (to test one)")
    ap.add_argument("--limit", type=int, default=0, help="max leads to POST (0 = all)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between POSTs")
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for UTP")
    ap.add_argument("--max-fail", type=int, default=5, help="stop after this many failures in a row")
    args = ap.parse_args()

    endpoint = os.environ.get("UTP_ENDPOINT_URL", "").strip().strip('"')
    api_key  = os.environ.get("UTP_API_KEY", "").strip()
    if args.send and (not endpoint or not api_key):
        print("UTP_ENDPOINT_URL and UTP_API_KEY must be set to --send.")
        return 2

    rows, sin_links = fetch_candidates(args.since, args.lead)
    if args.limit:
        rows = rows[:args.limit]

    print(f"Leads to re-POST: {len(rows)}  (since {args.since})")
    print(f"Rows with NULL validationId and no report links (not handled here): {sin_links}")
    if not rows:
        return 0

    if not args.send:
        print(f"\nDRY RUN — endpoint would be: {endpoint or '(UTP_ENDPOINT_URL not set)'}")
        for r in rows[:3]:
            print(f"\ntoken={r['token']}  processed_at={r['processed_at']}")
            print(json.dumps(build_payload(r), ensure_ascii=False, indent=2))
        print("\nRe-run with --send (try --limit 5 first).")
        return 0

    out_dir = ROOT / "scripts" / "outputs"  # gitignored ("outputs/"): contains lead IDs
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"recover_utp_{datetime.now():%Y%m%d_%H%M%S}.csv"
    headers = {"Content-Type": "application/json", "x-api-key": api_key}

    ok = fail = in_a_row = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["token", "lead_id", "http_status", "ok", "validationId", "response"])
        for i, r in enumerate(rows, start=1):
            status, body_text, vid = "", "", None
            try:
                resp = requests.post(endpoint, json=build_payload(r), headers=headers, timeout=args.timeout)
                status, body_text = resp.status_code, resp.text[:1000]
                if resp.ok:
                    try:
                        body = resp.json()
                        vid = body.get("validationId") or body.get("validation_id") or body.get("id")
                    except ValueError:
                        pass
            except requests.RequestException as e:
                status, body_text = "error", f"{type(e).__name__}: {e}"[:1000]

            if vid:
                try:
                    write_validation_id(r["token"], str(vid))
                except Exception as e:  # UTP has it; record it so it can be written by hand
                    body_text = f"DB WRITE FAILED: {e} | {body_text}"
                ok += 1
                in_a_row = 0
            else:
                fail += 1
                in_a_row += 1

            w.writerow([r["token"], r["lead_id"], status, bool(vid), vid or "", body_text])
            f.flush()
            print(f"[{i}/{len(rows)}] {r['lead_id']} → {status} {'✓ ' + str(vid) if vid else '✗ ' + body_text[:120]}")

            if in_a_row >= args.max_fail:
                print(f"\nStopped: {in_a_row} failures in a row. Check the responses in {out_path}")
                break
            if i < len(rows):
                time.sleep(args.delay)

    print(f"\nDone: {ok} recovered, {fail} failed. Detail: {out_path}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
