#!/usr/bin/env python3
"""
One-off: copies the "Status" tab of the Google Sheet into byw_reportgen_status.

Default is a DRY RUN: reads the whole sheet, checks every row against the
table's rules and prints a report. Nothing is written.

    python -m scripts.import_status_sheet                     # check only
    python -m scripts.import_status_sheet --apply             # check + import
    python -m scripts.import_status_sheet --apply --skip-bad  # import, leaving out rows that fail checks

Checks: header matches, every row has a Student ID and a readable Timestamp,
values fit their column, Legacy Links is valid JSON, Status is a known value,
and Student IDs repeated across rows.

Repeated Student IDs: the table keeps one row per student (like the sheet's
writer does), so only the newest one is imported. The others are written to
--dupes-csv so no record is lost. Rows left out by --skip-bad go to
--rejects-csv.

The import never overwrites a newer row: if a student already has a row in
the table (from the dual-write flush) with a later processed_at, it is kept.

Env vars: SHEETS_STATUS_ID (or --sheet-id), GOOGLE_CREDENTIALS_JSON,
          DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
Table DDL: scripts/sql/byw_reportgen_status.sql (run it first)
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline.status_db import HEADERS, DB_COLUMNS, TOTAL_COLS
from app.pipeline.sheets_updater import SHEET_TAB, get_sheets_service, _with_backoff

KNOWN_STATUSES = {"ok", "error", "resent", "recomputed"}

# Max length per column (VARCHAR limits from the DDL); None = MEDIUMTEXT.
MAX_LEN = {
    "student_id": 191, "nombre": 255, "email": 191, "rol": 64, "status": 16,
    "link_estudiante": 1024, "link_padres": 1024, "link_ccr": 1024, "link_input_json": 1024,
    "email_enviado": 32, "drive_upload": 32, "siteground_upload": 32,
    "error": None, "legacy_links_json": None,
}

TS_FORMATS = [
    "%Y-%m-%d %H:%M:%S UTC",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
]

# Same as status_db.UPSERT_SQL, but a row only replaces one that is not newer.
# processed_at must be assigned LAST: MySQL applies the assignments in order.
_GUARDED = [c for c in DB_COLUMNS if c not in ("student_id", "processed_at")] + ["processed_at"]
IMPORT_SQL = (
    f"INSERT INTO byw_reportgen_status ({', '.join(DB_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(DB_COLUMNS))}) "
    "ON DUPLICATE KEY UPDATE "
    + ", ".join(
        f"{c} = IF(VALUES(processed_at) >= processed_at, VALUES({c}), {c})" for c in _GUARDED
    )
)


def _cell(v) -> str:
    """UNFORMATTED_VALUE gives numbers as int/float; 12345.0 → "12345"."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v).strip()


def _parse_ts(s: str) -> datetime | None:
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def read_sheet(sheet_id: str) -> list[list]:
    service = get_sheets_service()
    result = _with_backoff(lambda: (
        service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB}!A:{chr(64 + TOTAL_COLS)}",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ).execute()
    ))
    return result.get("values") or []


def check_rows(rows: list[list]):
    """
    Returns (good, bad): good = [(sheet_row_number, params_tuple)],
    bad = [(sheet_row_number, raw_cells, [problems])].
    """
    good, bad = [], []
    for n, raw in enumerate(rows, start=2):  # row 1 is the header
        cells = [_cell(v) for v in raw][:TOTAL_COLS]
        cells += [""] * (TOTAL_COLS - len(cells))
        if not any(cells):
            continue  # fully blank line, nothing to import

        problems = []
        ts = _parse_ts(cells[0])
        if ts is None:
            problems.append(f"unreadable Timestamp {cells[0]!r}")
        if not cells[1]:
            problems.append("blank Student ID")
        if cells[5] not in KNOWN_STATUSES:
            problems.append(f"unknown Status {cells[5]!r}")
        if cells[14]:
            try:
                json.loads(cells[14])
            except ValueError:
                problems.append("Legacy Links is not valid JSON")
        if len(raw) > TOTAL_COLS and any(_cell(v) for v in raw[TOTAL_COLS:]):
            problems.append("has values past column O")
        for col, val in zip(DB_COLUMNS, cells):
            limit = MAX_LEN.get(col)
            if limit and len(val) > limit:
                problems.append(f"{col} is {len(val)} chars (max {limit})")

        # "Unknown status" alone is only a warning: the row still fits the table.
        blocking = [p for p in problems if not p.startswith("unknown Status")]
        if blocking:
            bad.append((n, cells, problems))
            continue
        if problems:
            print(f"  ⚠️  row {n}: {'; '.join(problems)}")

        params = [v if v != "" else None for v in cells]
        params[0] = ts
        params[1] = cells[1]
        params[5] = cells[5]  # NOT NULL in the table
        good.append((n, tuple(params)))
    return good, bad


def split_duplicates(good):
    """Newest row per Student ID (ties → lowest in the sheet), plus the rest."""
    by_id = defaultdict(list)
    for n, params in good:
        by_id[params[1]].append((n, params))
    keep, dupes = [], []
    for entries in by_id.values():
        entries.sort(key=lambda e: (e[1][0], e[0]))
        keep.append(entries[-1])
        dupes.extend(entries[:-1])
    return keep, dupes


def write_csv(path: str, header: list, rows: list[list]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet-id", default=os.environ.get("SHEETS_STATUS_ID"))
    ap.add_argument("--apply", action="store_true", help="write to MySQL (default: dry run)")
    ap.add_argument("--skip-bad", action="store_true", help="with --apply: import despite rows that fail checks")
    ap.add_argument("--dupes-csv", default="status_import_duplicates.csv")
    ap.add_argument("--rejects-csv", default="status_import_rejects.csv")
    ap.add_argument("--chunk", type=int, default=500)
    args = ap.parse_args()

    if not args.sheet_id:
        print("SHEETS_STATUS_ID / --sheet-id not set")
        return 2

    print(f"📥 Reading sheet {args.sheet_id} tab {SHEET_TAB!r}...")
    values = read_sheet(args.sheet_id)
    if not values:
        print("Sheet is empty, nothing to import")
        return 0

    header = [_cell(v) for v in values[0]]
    if header[:TOTAL_COLS] != HEADERS:
        print("❌ Header does not match the expected columns:")
        for i, (want, got) in enumerate(zip(HEADERS, header + [""] * TOTAL_COLS)):
            mark = "  " if want == got else "❌"
            print(f"   {mark} {chr(65 + i)}: expected {want!r}, found {got!r}")
        return 1

    good, bad = check_rows(values[1:])
    keep, dupes = split_duplicates(good)

    print()
    print("=" * 60)
    print(f"Rows in sheet (excluding header):  {len(values) - 1}")
    print(f"Rows passing checks:               {len(good)}")
    print(f"Rows failing checks:               {len(bad)}")
    print(f"Distinct Student IDs to import:    {len(keep)}")
    print(f"Older repeats of a Student ID:     {len(dupes)}")
    print(f"Status values:                     {dict(Counter(p[5] for _, p in keep))}")
    print("=" * 60)

    if bad:
        print("\nRows failing checks (first 50):")
        for n, _, problems in bad[:50]:
            print(f"  row {n}: {'; '.join(problems)}")
        write_csv(args.rejects_csv, ["sheet_row"] + HEADERS + ["problems"],
                  [[n] + cells + ["; ".join(p)] for n, cells, p in bad])
        print(f"  → all of them written to {args.rejects_csv}")

    if dupes:
        repeated = Counter(p[1] for _, p in dupes)
        print(f"\n{len(repeated)} Student IDs appear more than once (top 20):")
        for sid, extra in repeated.most_common(20):
            print(f"  {sid!r}: {extra + 1} rows")
        write_csv(args.dupes_csv, ["sheet_row"] + HEADERS,
                  [[n] + ["" if v is None else str(v) for v in p] for n, p in sorted(dupes)])
        print(f"  → older rows written to {args.dupes_csv} (only the newest is imported)")

    if not args.apply:
        print("\nDry run: nothing written. Re-run with --apply to import.")
        return 0
    if bad and not args.skip_bad:
        print("\n❌ Not importing: some rows fail checks. Fix them in the sheet, or pass --skip-bad.")
        return 1

    from app.pipeline.db import db_cursor

    params = [p for _, p in keep]
    print(f"\n📤 Importing {len(params)} rows in chunks of {args.chunk}...")
    for i in range(0, len(params), args.chunk):
        with db_cursor() as cur:  # one transaction per chunk: a failure keeps earlier chunks
            cur.executemany(IMPORT_SQL, params[i:i + args.chunk])
        print(f"   {min(i + args.chunk, len(params))}/{len(params)}")

    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM byw_reportgen_status")
        total = cur.fetchone()["n"]
        ids = [p[1] for p in params]
        missing = 0
        for i in range(0, len(ids), 1000):
            chunk = ids[i:i + 1000]
            cur.execute(
                f"SELECT COUNT(*) AS n FROM byw_reportgen_status WHERE student_id IN ({', '.join(['%s'] * len(chunk))})",
                chunk,
            )
            missing += len(chunk) - cur.fetchone()["n"]

    print(f"\n✅ Done. Table now has {total} rows; imported IDs missing from it: {missing}")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
