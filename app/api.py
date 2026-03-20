from fastapi import FastAPI, HTTPException, BackgroundTasks
from pathlib import Path
import json
import os
import logging
import re
import unicodedata
import requests

from app.pipeline.run_student_pipeline import run_student_pipeline
from app.pipeline.email_sender import send_report_email
from app.pipeline.drive_uploader import upload_pdf_to_drive
from app.pipeline.siteground_sender import send_report_to_siteground

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("reportgen")

REPORT_TITLES = {
    "estudiante": 'Reporte "Autoconocimiento"',
    "padres": 'Reporte "Autoconocimiento versión padres"',
    "ccr_rojo": "CCR EN BOXES",
    "ccr_amarillo": "CCR CALENTANDO MOTORES",
    "ccr_verde": "CCR A TODA MARCHA",
}

app = FastAPI()

APP_TMP_DIR = Path("/app/tmp/jobs")
APP_TMP_DIR.mkdir(parents=True, exist_ok=True)

logger.info("ok, let's go")


def safe_filename(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_\-]", "", text)
    return text


def post_process(job: dict, reports: dict, job_dir: Path, flags: dict):
    """
    Runs in the background after the PDF is ready.
    Uploads to Drive, sends to SiteGround, sends email.
    POSTs a status report to the AppsScript webhook when done.
    """
    student_id = job.get("student_id")
    email = job.get("Email") or job.get("email")
    upload_drive = flags.get("upload_drive", True)
    send_siteground = flags.get("send_siteground", True)
    send_email = flags.get("send_email", True)

    status = {
        "student_id": student_id,
        "nombre": job.get("Nombre y Apellido"),
        "email": email,
        "drive": {},        # { report_type: { ok: bool, link: str, error: str } }
        "siteground": {},   # { report_type: { ok: bool, error: str } }
        "email_sent": { "ok": False, "error": None },
    }

    # ── DRIVE UPLOAD ──────────────────────────────────────────────
    drive_links = {}

    for report_type, pdf_path in reports.items():
        post_title = REPORT_TITLES.get(report_type)
        if not post_title:
            continue

        if report_type.startswith("ccr"):
            folder_id = os.environ["DRIVE_FOLDER_CCR"]
        elif report_type == "estudiante":
            folder_id = os.environ["DRIVE_FOLDER_AUTO_EST"]
        elif report_type == "padres":
            folder_id = os.environ["DRIVE_FOLDER_AUTO_PAD"]
        else:
            continue

        if not upload_drive:
            status["drive"][report_type] = {"ok": False, "link": None, "error": "skipped by flag"}
            continue

        student_name = (
            job.get("Nombre y Apellido")
            or job.get("Nombre")
            or job.get("nombre")
            or student_id
        )
        safe_name = safe_filename(student_name)
        filename = f"{safe_name}_{report_type}_{student_id}.pdf"

        try:
            logger.info(f"⬆️ Uploading to Drive ({report_type})")
            link = upload_pdf_to_drive(
                pdf_path=pdf_path,
                target_folder_id=folder_id,
                filename=filename,
            )
            drive_links[report_type] = link
            status["drive"][report_type] = {"ok": True, "link": link, "error": None}
        except Exception as e:
            logger.exception(f"❌ Drive upload failed for {report_type}")
            status["drive"][report_type] = {"ok": False, "link": None, "error": str(e)}

    # ── SITEGROUND ────────────────────────────────────────────────
    for report_type, drive_link in drive_links.items():
        post_title = REPORT_TITLES.get(report_type)
        if not post_title:
            continue

        if not send_siteground:
            status["siteground"][report_type] = {"ok": False, "error": "skipped by flag"}
            continue

        try:
            logger.info(f"🌐 Sending {report_type} to SiteGround")
            send_report_to_siteground(
                email=email,
                drive_link=drive_link,
                post_title=post_title,
            )
            status["siteground"][report_type] = {"ok": True, "error": None}
        except Exception as e:
            logger.exception(f"⚠️ SiteGround failed for {report_type}")
            status["siteground"][report_type] = {"ok": False, "error": str(e)}

    # ── EMAIL ─────────────────────────────────────────────────────
    email_pdfs = [str(v) for v in reports.values() if str(v).endswith(".pdf")]

    if not send_email:
        status["email_sent"] = {"ok": False, "error": "skipped by flag"}
    elif not email_pdfs:
        status["email_sent"] = {"ok": False, "error": "no PDFs found"}
    else:
        try:
            send_report_email(to_email=email, pdf_paths=email_pdfs, student=job)
            status["email_sent"] = {"ok": True, "error": None}
        except Exception as e:
            logger.exception("❌ Email failed")
            status["email_sent"] = {"ok": False, "error": str(e)}

    # ── SAVE STATUS ───────────────────────────────────────────────
    with open(job_dir / "status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)

    # ── PING APPSSCRIPT WEBHOOK ───────────────────────────────────
    webhook_url = os.environ.get("APPSSCRIPT_WEBHOOK_URL")
    if webhook_url:
        try:
            logger.info("📡 Pinging AppsScript webhook")
            requests.post(webhook_url, json=status, timeout=10)
            logger.info("✅ Webhook delivered")
        except Exception as e:
            logger.exception("⚠️ Webhook delivery failed")
    else:
        logger.warning("No APPSSCRIPT_WEBHOOK_URL set, skipping webhook")


@app.get("/health")
def health():
    return {
        "pipeline_exists": (Path("/app/pipeline")).exists(),
        "assets_exists": (Path("/app/assets")).exists(),
        "tmp_exists": (Path("/app/tmp")).exists(),
    }


@app.get("/status/{job_id}")
def get_status(job_id: str):
    status_file = APP_TMP_DIR / job_id / "status.json"
    if not status_file.exists():
        return {"status": "processing"}
    with open(status_file) as f:
        return json.load(f)


@app.post("/run")
def run_student(job: dict, background_tasks: BackgroundTasks):
    student_id = job.get("student_id")
    job_id = job.get("job_id") or student_id

    if not student_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing student_id or job_id")

    job_dir = APP_TMP_DIR / job_id
    email = job.get("Email") or job.get("email")

    flags = job.get("flags", {})

    try:
        job_dir.mkdir(parents=True, exist_ok=True)

        with open(job_dir / "input.json", "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)

        logger.info("⚙️ Starting run_student_pipeline")
        pdf_paths, report_types = run_student_pipeline(job, job_dir)

        logger.info(f"📄 report_types={report_types} pdf_paths={[str(p) for p in pdf_paths]}")

        reports = dict(zip(report_types, pdf_paths))

        # ✅ PDF is ready — return immediately to AppsScript
        background_tasks.add_task(post_process, job, reports, job_dir, flags)

        return {
            "status": "ok",
            "job_id": job_id,
            "pdf_generated": report_types,
        }

    except Exception as e:
        logger.exception("💥 Unhandled exception in /run")
        with open(job_dir / "error.txt", "w", encoding="utf-8") as f:
            f.write(str(e))
        raise HTTPException(status_code=500, detail="Internal error while generating report")