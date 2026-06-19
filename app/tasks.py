import os
import json
import logging
import traceback
from pathlib import Path

from app.pipeline.run_student_pipeline import run_student_pipeline
from app.pipeline.build_pptx import determine_template
from app.pipeline.email_sender import send_report_email
from app.pipeline.drive_uploader import upload_pdf_to_drive, upsert_json_to_drive
from app.pipeline.drive_downloader import download_drive_file
from app.pipeline.siteground_sender import send_report_to_siteground, upload_pdf_to_siteground
from app.pipeline.sheets_updater import update_student_status
from app.pipeline.student_historic import get_all_links, upsert_student
from app.pipeline.db_writer import write_majors_to_db, post_utp_payload, alter_table_reports
from app.pipeline.historic_db_writer import upsert_historico

logger = logging.getLogger("reportgen.tasks")

APP_TMP_DIR = Path("/app/tmp/jobs")
APP_TMP_DIR.mkdir(parents=True, exist_ok=True)

UTP_ROLES = {"UTP"}

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


def safe_filename(text: str) -> str:
    import unicodedata, re
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_\-\.]", "", text)
    return text


def process_report_job(job: dict):
    """
    Función que ejecuta el worker de RQ.
    Contiene toda la lógica que antes vivía en /run + _post_pipeline_tasks.
    """
    student_id = job.get("student_id")
    job_id     = job.get("job_id") or student_id
    email      = job.get("Email") or job.get("email")
    name       = (
        job.get("Nombre y Apellido")
        or job.get("Nombre")
        or job.get("nombre")
        or job.get("nombre_estudiante")
        or student_id
    )
    rol        = job.get("Rol", "")
    is_utp     = rol in UTP_ROLES

    flag_send_email      = job.get("send_email",      True)
    flag_upload_drive    = job.get("upload_drive",    True)
    flag_post_siteground = job.get("post_siteground", True)
    flag_upload_historic = job.get("upload_historic", True)
    flag_force_rerun     = job.get("force_rerun",     False)

    job_dir = APP_TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    logger.info("⚙️  Worker starting job | job_id=%s student=%s", job_id, name)

    # ---------------------------------------------------------------
    # HISTORIC fallback
    # ---------------------------------------------------------------
    if not flag_force_rerun and email:
        historic_links  = get_all_links(email)
        expected_types  = set(dict(determine_template(job, Path("/app/assets"))).keys())
        available_types = set(historic_links.keys())

        if historic_links and expected_types.issubset(available_types):
            logger.info("📖 Historic hit for %s — resending", email)
            if flag_send_email:
                downloaded_pdfs = []
                failed_types    = []
                for report_type, drive_link in historic_links.items():
                    filename = REPORT_FILENAMES.get(report_type, f"{report_type}.pdf")
                    try:
                        downloaded_pdfs.append(download_drive_file(drive_link, filename))
                    except Exception:
                        failed_types.append(report_type)

                if not failed_types:
                    send_report_email(to_email=email, pdf_paths=downloaded_pdfs, student=job)
                    update_student_status(
                        student_id, job,
                        status="resent", email_sent="yes",
                        drive_uploaded="yes", siteground_uploaded="skipped",
                        drive_links=historic_links,
                    )
                    return {"status": "resent", "job_id": job_id}

    # ---------------------------------------------------------------
    # PIPELINE (blocking — LibreOffice)
    # ---------------------------------------------------------------
    with open(job_dir / "input.json", "w", encoding="utf-8") as f:
        json.dump(job, f, indent=2, ensure_ascii=False)

    try:
        pdf_paths, report_types = run_student_pipeline(job, job_dir)
        logger.info("📄 PDFs done: %s", report_types)

        if rol in ("counseling", "gs_actividades"):
            upsert_historico(job)
        
    except Exception as e:
        logger.exception("💥 Pipeline failed for %s", job_id)
        update_student_status(student_id, job, status="error", error_msg=str(e))
        with open(job_dir / "error.txt", "w") as f:
            f.write(traceback.format_exc())
        raise  # RQ marca el job como failed y aplica retry

    reports       = dict(zip(report_types, pdf_paths))
    drive_links   = {}
    sg_file_links = {}
    email_sent    = "no"
    drive_uploaded   = "no"
    sg_uploaded      = "no"
    input_json_link  = ""

    # ---------------------------------------------------------------
    # WRITE MAJORS TO DB
    # ---------------------------------------------------------------
    if rol not in {"Verde", "Rojo", "Amarillo"}:
        try:
            write_majors_to_db(job)
        except Exception:
            logger.exception("⚠️  write_majors_to_db failed (non-fatal)")

    # ---------------------------------------------------------------
    # INPUT JSON → Drive
    # ---------------------------------------------------------------
    inputs_folder_id = os.environ.get("DRIVE_FOLDER_INPUTS")
    if inputs_folder_id and flag_upload_historic:
        try:
            safe_email      = safe_filename(email or "no_email")
            input_filename  = f"{safe_email}_{student_id}_input.json"
            input_json_link = upsert_json_to_drive(
                data=job, filename=input_filename, folder_id=inputs_folder_id
            )
        except Exception:
            logger.exception("⚠️  Input JSON upload failed (non-fatal)")

    # ---------------------------------------------------------------
    # UTP BRANCH
    # ---------------------------------------------------------------
    if is_utp:
        user_email = (job.get("user_email") or "")
        if user_email:
            logger.exception("the user email is %s", user_email)

        for report_type, pdf_path in reports.items():
            filename = f"{safe_filename(name)}_{report_type}_{student_id}.pdf"
            try:
                sg_file_links[report_type] = upload_pdf_to_siteground(pdf_path, filename)
            except Exception:
                logger.exception("⚠️  SiteGround upload failed for %s", report_type)

        if sg_file_links and user_email:
            try:
                post_utp_payload(student_id, user_email, job, sg_file_links)
            except Exception:
                logger.exception("⚠️  post_utp_payload failed (non-fatal)")
            try:
                alter_table_reports(user_email=user_email, links=sg_file_links)
            except Exception:
                logger.exception("⚠️  alter_table_reports failed (non-fatal)")
            try:
                from app.pipeline.email_sender import send_utp_student_email
                reporte_url = sg_file_links.get("estudiante", "")
                if reporte_url:
                    send_utp_student_email(cedula=user_email, reporte_url=reporte_url)
            except Exception:
                logger.exception("⚠️  send_utp_student_email failed (non-fatal)")

        drive_links    = sg_file_links
        drive_uploaded = "yes (siteground)" if sg_file_links else "no"
        sg_uploaded    = "skipped"

    # ---------------------------------------------------------------
    # STANDARD BRANCH
    # ---------------------------------------------------------------
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
                    drive_links[report_type] = upload_pdf_to_drive(
                        pdf_path=pdf_path, target_folder_id=folder_id, filename=filename
                    )
                except Exception:
                    logger.exception("⚠️  Drive upload failed for %s", report_type)

            drive_uploaded = "yes" if drive_links else "no"
        else:
            drive_uploaded = "skipped"

        if flag_post_siteground and drive_links:
            sg_ok = 0
            for report_type, drive_link in drive_links.items():
                post_title = REPORT_TITLES.get(report_type)
                if not post_title:
                    continue
                try:
                    send_report_to_siteground(email=email, drive_link=drive_link, post_title=post_title)
                    sg_ok += 1
                except Exception:
                    logger.exception("⚠️  SiteGround failed for %s", report_type)
            sg_uploaded = "yes" if sg_ok else "no"
        else:
            sg_uploaded = "skipped"

    # ---------------------------------------------------------------
    # HISTORIC upsert
    # ---------------------------------------------------------------
    if drive_links:
        upsert_student(email=email, student_id=student_id, name=name, drive_links=drive_links)

    # ---------------------------------------------------------------
    # EMAIL
    # ---------------------------------------------------------------
    if flag_send_email and not is_utp:
        email_pdfs = [p for p in pdf_paths if Path(p).exists()]
        if email and email_pdfs:
            try:
                send_report_email(to_email=email, pdf_paths=email_pdfs, student=job)
                email_sent = "yes"
            except Exception:
                logger.exception("⚠️  Email failed")
        else:
            logger.warning("No email sent — email=%s pdf_count=%s", email, len(email_pdfs))
    else:
        email_sent = "skipped"

    # ---------------------------------------------------------------
    # STATUS SHEET
    # ---------------------------------------------------------------
    update_student_status(
        student_id, job,
        status="ok",
        email_sent=email_sent,
        drive_uploaded=drive_uploaded,
        siteground_uploaded=sg_uploaded,
        drive_links=drive_links,
        input_json_link=input_json_link,
    )

    logger.info("✅ Job complete: %s", job_id)
    return {"status": "ok", "job_id": job_id, "report_types": report_types}
