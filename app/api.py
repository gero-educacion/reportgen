from fastapi import FastAPI, HTTPException, BackgroundTasks
from datetime import datetime, timezone
from pathlib import Path
import json
import traceback
import os
import logging
import re
import shutil
import unicodedata
import time

from app.pipeline.run_student_pipeline import run_student_pipeline
from app.pipeline.build_pptx import determine_template
from app.pipeline.email_sender import send_report_email
from app.pipeline.drive_uploader import upload_pdf_to_drive, upsert_json_to_drive
from app.pipeline.drive_downloader import download_drive_file
from app.pipeline.siteground_sender import send_report_to_siteground, upload_pdf_to_siteground
from app.pipeline.sheets_updater import update_student_status
from app.pipeline.student_historic import get_all_links, upsert_student
from app.pipeline.db_writer import write_majors_to_db, post_utp_payload, alter_table_reports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("reportgen")

REPORT_TITLES = {
    "estudiante":   'Reporte "Autoconocimiento"',
    "padres":       'Reporte "Autoconocimiento versión padres"',
    "ccr_rojo":     "CCR EN BOXES",
    "ccr_amarillo": "CCR CALENTANDO MOTORES",
    "ccr_verde":    "CCR A TODA MARCHA",
}

REPORT_FILENAMES = {
    "estudiante":   "Reporte_Autoconocimiento.pdf",
    "padres":       "Reporte_Autoconocimiento_Padres.pdf",
    "ccr_rojo":     "CCR_En_Boxes.pdf",
    "ccr_amarillo": "CCR_Calentando_Motores.pdf",
    "ccr_verde":    "CCR_A_Toda_Marcha.pdf",
}

# Roles that get files hosted on SiteGround instead of Drive
UTP_ROLES = {"UTP"}

app = FastAPI()

APP_TMP_DIR = Path("/app/tmp/jobs")
APP_TMP_DIR.mkdir(parents=True, exist_ok=True)

LOCK_FILENAME = ".lock"

logger.info("ok, let's go")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_filename(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_\-\.]", "", text)
    return text


def _acquire_lock(job_dir: Path, timeout: int = 90) -> bool:
    lock_path = job_dir / LOCK_FILENAME
    deadline  = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(1)
    return False


def _release_lock(job_dir: Path):
    try:
        (job_dir / LOCK_FILENAME).unlink(missing_ok=True)
    except Exception:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Background task — runs after 200 OK is returned
# ---------------------------------------------------------------------------

def _post_pipeline_tasks(
    job: dict,
    student_id: str,
    email: str,
    name: str,
    job_dir: Path,
    pdf_paths: list[Path],
    report_types: list[str],
    flag_send_email: bool,
    flag_upload_drive: bool,
    flag_post_siteground: bool,
    flag_upload_historic: bool,
):
    rol = job.get("Rol", "")
    is_utp = rol in UTP_ROLES

    reports = dict(zip(report_types, pdf_paths))
    drive_links      = {}
    sg_file_links    = {}   # SiteGround file hosting links (UTP)
    input_json_link  = ""
    email_sent       = "no"
    drive_uploaded   = "no"
    sg_uploaded      = "no"

    # ----------------------------------------------------------
    # WRITE MAJORS TO DB  (all students that have carrera fields)
    # ----------------------------------------------------------
    if rol != "Verde" and rol != "Rojo" and rol != "Amarillo":
        try:
            write_majors_to_db(job)
        except Exception:
            logger.exception("⚠️  write_majors_to_db failed (non-fatal)")

    # ----------------------------------------------------------
    # INPUT JSON → Drive
    # ----------------------------------------------------------
    inputs_folder_id = os.environ.get("DRIVE_FOLDER_INPUTS")
    if inputs_folder_id and flag_upload_historic:
        try:
            safe_email     = safe_filename(email or "no_email")
            input_filename = f"{safe_email}_{student_id}_input.json"
            input_json_link = upsert_json_to_drive(
                data=job,
                filename=input_filename,
                folder_id=inputs_folder_id,
            )
            logger.info("📦 Input JSON uploaded: %s", input_filename)
        except Exception:
            logger.exception("⚠️  Failed to upload input JSON (non-fatal)")

    # ----------------------------------------------------------
    # UTP BRANCH — upload PDFs to SiteGround filesystem
    # ----------------------------------------------------------
    if is_utp:
        logger.info("🏫 UTP student — uploading PDFs to SiteGround filesystem")
        for report_type, pdf_path in reports.items():
            filename = f"{safe_filename(name)}_{report_type}_{student_id}.pdf"
            try:
                public_url = upload_pdf_to_siteground(pdf_path, filename)
                sg_file_links[report_type] = public_url
                logger.info("⬆️  SiteGround file upload ok: %s → %s", report_type, public_url)
            except Exception:
                logger.exception("⚠️  SiteGround file upload failed for %s", report_type)

        # Post UTP-specific payload to dedicated endpoint
        if sg_file_links:
            try:
                post_utp_payload(
                    student_id=student_id,
                    student=job,
                    report_links=sg_file_links,
                )
            except Exception:
                logger.exception("⚠️  post_utp_payload failed (non-fatal)")

            try:
                alter_table_reports(
                    user_email = email,
                    links = sg_file_links,
                )
            except Exception:
                logger.exception("Los links no se subieron a byw_tracking_algoritmo_AC")

        # For sheet tracking, treat sg_file_links as drive_links
        drive_links    = sg_file_links
        drive_uploaded = "yes (siteground)" if sg_file_links else "no"
        sg_uploaded    = "skipped"          # siteground_sender not used for UTP

    # ----------------------------------------------------------
    # STANDARD BRANCH — upload PDFs to Google Drive
    # ----------------------------------------------------------
    else:
        if flag_upload_drive:
            for report_type, pdf_path in reports.items():
                post_title = REPORT_TITLES.get(report_type)
                if not post_title:
                    continue

                if report_type.startswith("ccr"):
                    folder_id = os.environ.get("DRIVE_FOLDER_CCR")
                elif report_type == "estudiante":
                    folder_id = os.environ.get("DRIVE_FOLDER_AUTO_EST")
                elif report_type == "padres":
                    folder_id = os.environ.get("DRIVE_FOLDER_AUTO_PAD")
                else:
                    continue

                filename = f"{safe_filename(name)}_{report_type}_{student_id}.pdf"
                try:
                    drive_link = upload_pdf_to_drive(
                        pdf_path=pdf_path,
                        target_folder_id=folder_id,
                        filename=filename,
                    )
                    drive_links[report_type] = drive_link
                    logger.info("⬆️  Uploaded %s to Drive", report_type)
                except Exception:
                    logger.exception("⚠️  Drive upload failed for %s", report_type)

            drive_uploaded = "yes" if drive_links else "no"
        else:
            logger.info("⏭️  Drive upload skipped (flag=False)")
            drive_uploaded = "skipped"

        # SiteGround notification (existing sender — posts link to WP)
        if flag_post_siteground and drive_links:
            sg_successes = 0
            for report_type, drive_link in drive_links.items():
                post_title = REPORT_TITLES.get(report_type)
                if not post_title:
                    continue
                try:
                    send_report_to_siteground(
                        email=email,
                        drive_link=drive_link,
                        post_title=post_title,
                    )
                    sg_successes += 1
                    logger.info("🌐 SiteGround ok: %s", report_type)
                except Exception:
                    logger.exception("⚠️  SiteGround failed for %s", report_type)
            sg_uploaded = "yes" if sg_successes > 0 else "no"
        elif not flag_post_siteground:
            logger.info("⏭️  SiteGround skipped (flag=False)")
            sg_uploaded = "skipped"

    # ----------------------------------------------------------
    # HISTORIC  (all students)
    # ----------------------------------------------------------
    if drive_links:
        upsert_student(
            email=email,
            student_id=student_id,
            name=name,
            drive_links=drive_links,
        )

    # ----------------------------------------------------------
    # EMAIL  (all students)
    # ----------------------------------------------------------
    if flag_send_email and not is_utp:
        email_pdfs = [p for p in pdf_paths if Path(p).exists()]
        if email and email_pdfs:
            try:
                send_report_email(
                    to_email=email,
                    pdf_paths=email_pdfs,
                    student=job,
                )
                email_sent = "yes"
            except Exception:
                logger.exception("⚠️  Email failed")
                email_sent = "no"
        else:
            logger.warning("No email sent — email=%s pdf_count=%s", email, len(email_pdfs))
    else:
        logger.info("⏭️  Email skipped (flag=False)")
        email_sent = "skipped"

    # ----------------------------------------------------------
    # STATUS SHEET
    # ----------------------------------------------------------
    update_student_status(
        student_id, job,
        status="ok",
        email_sent=email_sent,
        drive_uploaded=drive_uploaded,
        siteground_uploaded=sg_uploaded,
        drive_links=drive_links,
        input_json_link=input_json_link,
    )

    logger.info("✅ Background tasks complete for %s", student_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "pipeline_exists": Path("/app/app/pipeline").exists(),
        "assets_exists":   Path("/app/assets").exists(),
        "tmp_exists":      Path("/app/tmp").exists(),
        "data_exists":     Path("/app/data").exists(),
    }


@app.post("/run")
def run_student(job: dict, background_tasks: BackgroundTasks):
    student_id = job.get("student_id")
    job_id     = job.get("job_id") or student_id

    if not student_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing student_id or job_id")

    flag_send_email      = job.get("send_email",      True)
    flag_upload_drive    = job.get("upload_drive",    True)
    flag_post_siteground = job.get("post_siteground", True)
    flag_force_rerun     = job.get("force_rerun",     False)
    flag_upload_historic = job.get("upload_historic", True)

    email = job.get("Email") or job.get("email")
    name  = (
        job.get("Nombre y Apellido")
        or job.get("Nombre")
        or job.get("nombre")
        or student_id
        or job.get("nombre_estudiante")
    )

    job_dir = APP_TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # FALLBACK — check students_historic.json
    # ------------------------------------------------------------------
    if not flag_force_rerun and email:
        historic_links = get_all_links(email)

        # Only use historic if it covers the report types this job needs
        expected_types  = set(dict(determine_template(job, Path("/app/assets"))).keys())
        available_types = set(historic_links.keys())

        if historic_links and expected_types.issubset(available_types):
            logger.info("📖 Historic record found for %s", email)

            if flag_send_email:
                downloaded_pdfs: list[Path] = []
                failed_types:    list[str]  = []

                for report_type, drive_link in historic_links.items():
                    filename = REPORT_FILENAMES.get(report_type, f"{report_type}.pdf")
                    try:
                        tmp_pdf = download_drive_file(drive_link, filename)
                        downloaded_pdfs.append(tmp_pdf)
                    except FileNotFoundError:
                        logger.warning("⚠️  Drive file missing for %s — will recompute", report_type)
                        failed_types.append(report_type)
                    except Exception:
                        logger.exception("⚠️  Download error for %s — will recompute", report_type)
                        failed_types.append(report_type)

                if failed_types:
                    logger.warning("🔁 Recomputing — missing: %s", failed_types)
                    update_student_status(
                        student_id, job,
                        status="recomputed",
                        error_msg=f"Drive files missing, recomputed: {', '.join(failed_types)}",
                    )
                    for p in downloaded_pdfs:
                        p.unlink(missing_ok=True)
                else:
                    background_tasks.add_task(
                        _send_resent_email,
                        job, student_id, email,
                        downloaded_pdfs, historic_links,
                    )
                    return {
                        "status":  "ok (resent — background)",
                        "job_id":  job_id,
                        "sources": list(historic_links.keys()),
                    }

    # ------------------------------------------------------------------
    # LOCK
    # ------------------------------------------------------------------
    if not _acquire_lock(job_dir, timeout=90):
        raise HTTPException(
            status_code=409,
            detail="Another request is already processing this student.",
        )

    try:
        with open(job_dir / "input.json", "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)

        # ----------------------------------------------------------
        # PIPELINE  (blocking — LibreOffice needs this)
        # ----------------------------------------------------------
        logger.info("⚙️  Starting pipeline | job_id=%s", job_id)
        pdf_paths, report_types = run_student_pipeline(job, job_dir)
        logger.info("📄 PDFs done: %s", report_types)

        # ----------------------------------------------------------
        # Hand off everything else to background and return immediately
        # ----------------------------------------------------------
        background_tasks.add_task(
            _post_pipeline_tasks,
            job, student_id, email, name,
            job_dir, pdf_paths, report_types,
            flag_send_email, flag_upload_drive,
            flag_post_siteground, flag_upload_historic,
        )

        return {
            "status":        "ok",
            "job_id":        job_id,
            "pdf_generated": report_types,
        }

    except Exception as e:
        logger.exception("💥 Unhandled exception in /run")

        update_student_status(
            student_id, job,
            status="error",
            error_msg=str(e),
        )

        with open(job_dir / "error.txt", "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())

        raise HTTPException(
            status_code=500,
            detail="Internal error while generating report",
        )

    finally:
        _release_lock(job_dir)


def _send_resent_email(
    job: dict,
    student_id: str,
    email: str,
    downloaded_pdfs: list[Path],
    historic_links: dict,
):
    """Background task for resending from historic."""
    try:
        send_report_email(
            to_email=email,
            pdf_paths=downloaded_pdfs,
            student=job,
        )
        update_student_status(
            student_id, job,
            status="resent",
            email_sent="yes",
            drive_uploaded="yes",
            siteground_uploaded="skipped",
            drive_links=historic_links,
        )
    except Exception:
        logger.exception("⚠️  Resend email failed")
    finally:
        for p in downloaded_pdfs:
            try:
                shutil.rmtree(p.parent, ignore_errors=True)
            except Exception:
                pass