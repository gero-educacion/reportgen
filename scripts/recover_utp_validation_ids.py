#!/usr/bin/env python3
"""
recover_utp_validation_ids.py
-----------------------------
One-off recovery (2026-11-19): get back the validationId of UTP students whose
report was generated and emailed, but whose byw_tracking_algoritmo_AC row has
no validationId.

Why it's not a plain re-POST
  UTP's endpoint answers 409 payload_conflict ("LeadId already exists with
  different content. Update is not allowed.") for these leads: one of our
  earlier POSTs reached them, but we never stored the answer. Every copy of a
  lead was reprocessed with a new token, and the report links contain the
  token (<base>/<name>_<type>_<token>.pdf), so each copy's payload differs.
  The script rebuilds the exact payload of EACH copy of the lead (its own
  historico + links rebuilt with its own token) and tries them oldest first,
  until UTP accepts one. Sending content UTP already has changes nothing on
  their side; a wrong guess just gets another 409.

Which leads
  client = 'UTP', validationId IS NULL, reporte_estudiante filled, processed
  since --since, and the lead has no validationId on any row.

What it writes
  validationId on the row whose payload UTP accepted, matched by token.
  Every attempt goes to scripts/outputs/recover_utp_<timestamp>.csv (gitignored:
  contains lead IDs) with the HTTP status and UTP's response.

Safety
  Dry run by default (prints the candidates it would try). --send to POST.
  Paced (--delay between POSTs), at most --max-candidates tries per lead, and
  stops after --max-fail leads in a row end in an error other than 409.

Env (same as reportgen): DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME
                         UTP_ENDPOINT_URL UTP_API_KEY
Run from the reportgen root (Windows: `py -3`, not `python`):
  railway run py -3 scripts/recover_utp_validation_ids.py                        # dry run
  railway run py -3 scripts/recover_utp_validation_ids.py --send --lead <lead_id>
  railway run py -3 scripts/recover_utp_validation_ids.py --send --limit 20
  railway run py -3 scripts/recover_utp_validation_ids.py --send
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:  # local runs: pick up reportgen/.env if python-dotenv is installed (never overrides real env)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.pipeline.db import db_cursor  # noqa: E402

TABLE = "byw_tracking_algoritmo_AC"

# <base>/<safe_name>_<type>_<token>.pdf — the token has hyphens, never underscores.
LINK_TOKEN = re.compile(r"_([^_/]+)\.pdf$")


def fetch_leads(since: str, lead: str | None) -> tuple[list[str], int]:
    """Returns (lead_ids to recover, oldest first; NULL rows without links)."""
    where_lead = "AND t.email = %s" if lead else ""
    params = [since] + ([lead] if lead else [])
    with db_cursor() as cur:
        cur.execute(f"""
            SELECT t.email AS lead_id, MIN(t.processed_at) AS first_at
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
            GROUP BY t.email
            ORDER BY first_at ASC
        """, params)
        leads = [r["lead_id"] for r in cur.fetchall()]

        cur.execute(f"""
            SELECT COUNT(*) AS n FROM {TABLE}
            WHERE client = 'UTP' AND validationId IS NULL
              AND NULLIF(reporte_estudiante, '') IS NULL
              AND processed_at >= %s
        """, [since])
        sin_links = cur.fetchone()["n"]
    return leads, sin_links


def fetch_rows(lead_id: str) -> list[dict]:
    """Every processed copy of this lead, oldest first."""
    with db_cursor() as cur:
        cur.execute(f"""
            SELECT token, carrera_1, reporte_estudiante, reporte_padres, historico, processed_at
            FROM {TABLE}
            WHERE client = 'UTP' AND email = %s
            ORDER BY processed_at ASC, token ASC
        """, [lead_id])
        return cur.fetchall()


def relink(url: str, token: str) -> str | None:
    """Same report URL, for another copy's token."""
    m = LINK_TOKEN.search(url or "")
    return url[:m.start()] + f"_{token}.pdf" if m else None


def candidates(lead_id: str, rows: list[dict]) -> list[tuple[str, dict]]:
    """[(token, payload)] — one per copy of the lead, oldest first, duplicates removed."""
    tmpl = next((r for r in reversed(rows) if (r["reporte_estudiante"] or "").strip()), None)
    if not tmpl:
        return []
    out, seen = [], set()
    for r in rows:
        try:
            job = json.loads(r["historico"] or "{}")
        except (TypeError, ValueError):
            job = {}
        est = relink(tmpl["reporte_estudiante"], r["token"]) or (r["reporte_estudiante"] or "")
        pad = relink(tmpl["reporte_padres"] or "", r["token"]) or (r["reporte_padres"] or "")
        payload = {  # same shape as db_writer.post_utp_payload
            "leadId":            lead_id,
            "career1":           job.get("CARRERA_01") or job.get("Carrera 01") or (r["carrera_1"] or "").upper(),
            "career2":           "",
            "resultsLink":       est,
            "resultsLinkParent": pad,
            "telephone":         job.get("Telefono") or "",
            "email":             job.get("Email_formulario") or "",
        }
        key = json.dumps(payload, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append((r["token"], payload))
    return out


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
    ap.add_argument("--limit", type=int, default=0, help="max leads (0 = all)")
    ap.add_argument("--max-candidates", type=int, default=10, help="max payloads tried per lead")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between POSTs")
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for UTP")
    ap.add_argument("--max-fail", type=int, default=5, help="stop after this many leads in a row end in a non-409 error")
    args = ap.parse_args()

    endpoint = os.environ.get("UTP_ENDPOINT_URL", "").strip().strip('"')
    api_key  = os.environ.get("UTP_API_KEY", "").strip()
    if args.send and (not endpoint or not api_key):
        print("UTP_ENDPOINT_URL and UTP_API_KEY must be set to --send.")
        return 2

    leads, sin_links = fetch_leads(args.since, args.lead)
    if args.limit:
        leads = leads[:args.limit]
    print(f"Leads to recover: {len(leads)}  (since {args.since})")
    print(f"Rows with NULL validationId and no report links (not handled here): {sin_links}")
    if not leads:
        return 0

    if not args.send:
        print(f"\nDRY RUN — endpoint would be: {endpoint or '(UTP_ENDPOINT_URL not set)'}")
        for lead in leads[:2]:
            cands = candidates(lead, fetch_rows(lead))
            print(f"\nlead {lead}: {len(cands)} candidate payload(s), oldest first")
            for token, p in cands[:3]:
                print(f"  token={token}")
                print("  " + json.dumps(p, ensure_ascii=False))
        print("\nRe-run with --send --lead <one lead> first.")
        return 0

    out_dir = ROOT / "scripts" / "outputs"  # gitignored ("outputs/"): contains lead IDs
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"recover_utp_{datetime.now():%Y%m%d_%H%M%S}.csv"
    headers = {"Content-Type": "application/json", "x-api-key": api_key}

    recovered = no_match = errors = errors_in_a_row = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lead_id", "token", "attempt", "http_status", "validationId", "response"])

        for i, lead in enumerate(leads, start=1):
            cands = candidates(lead, fetch_rows(lead))[:args.max_candidates]
            outcome = "no_candidates" if not cands else "no_match"

            for n, (token, payload) in enumerate(cands, start=1):
                status, body_text, vid, code = "", "", None, ""
                try:
                    resp = requests.post(endpoint, json=payload, headers=headers, timeout=args.timeout)
                    status, body_text = resp.status_code, resp.text[:1000]
                    try:
                        body = resp.json()
                    except ValueError:
                        body = {}
                    code = body.get("code", "") if isinstance(body, dict) else ""
                    if resp.ok and isinstance(body, dict):
                        vid = body.get("validationId") or body.get("validation_id") or body.get("id")
                except requests.RequestException as e:
                    status, body_text = "error", f"{type(e).__name__}: {e}"[:1000]

                w.writerow([lead, token, n, status, vid or "", body_text])
                f.flush()
                time.sleep(args.delay)

                if vid:
                    try:
                        write_validation_id(token, str(vid))
                    except Exception as e:  # UTP has it; it's in the CSV, write it by hand
                        print(f"   DB write failed for {token}: {e}")
                    outcome = f"✓ {vid} (copy {n}/{len(cands)})"
                    break
                if status == 409 and code == "payload_conflict":
                    continue  # not the content UTP has: try the next copy
                outcome = f"error {status}: {body_text[:120]}"
                break

            if outcome.startswith("✓"):
                recovered += 1
                errors_in_a_row = 0
            elif outcome.startswith("error"):
                errors += 1
                errors_in_a_row += 1
            else:
                no_match += 1
                errors_in_a_row = 0
            print(f"[{i}/{len(leads)}] {lead} → {outcome}")

            if errors_in_a_row >= args.max_fail:
                print(f"\nStopped: {errors_in_a_row} leads in a row ended in errors. See {out_path}")
                break

    print(f"\nDone: {recovered} recovered, {no_match} no matching copy, {errors} errors. Detail: {out_path}")
    return 0 if (no_match == 0 and errors == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
