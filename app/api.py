from fastapi import FastAPI, HTTPException
from datetime import datetime
from pathlib import Path
import json
import traceback
import os
import logging
import re
import unicodedata

from app.pipeline.run_student_pipeline import run_student_pipeline
from app.pipeline.email_sender import send_report_email
from app.pipeline.drive_uploader import upload_pdf_to_drive
from app.pipeline.siteground_sender import send_report_to_siteground

print("🔥 /run endpoint ENTERED")

print("ENV VARS SEEN:")
for k in ["GOOGLE_APPLICATION_CREDENTIALS", "DRIVE_FOLDER_AUTO_EST"]:
    print(f"  {k} =", os.environ.get(k))

cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
print("CRED PATH =", cred)
print("CRED EXISTS =", os.path.exists(cred) if cred else None)

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
    # normalize accents → ascii
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")

    # lowercase, replace spaces
    text = text.lower().strip()
    text = re.sub(r"\s+", "_", text)

    # remove anything not safe
    text = re.sub(r"[^a-z0-9_\-]", "", text)

    return text



@app.get("/health")
def health():
    return {
        "pipeline_exists": (Path("/app/pipeline")).exists(),
        "assets_exists": (Path("/app/assets")).exists(),
        "tmp_exists": (Path("/app/tmp")).exists(),
    }

@app.post("/run")
def run_student(job: dict):
    """
    Recibe el json del estudiante y renderiza su reporte...
    Puede ser que sea una actividad que requiera un reporte de padres, y se computará pero no se 
    le envía por mail dado que why would we expose him like that?
    """

    student_id = job.get("student_id")
    job_id = job.get("job_id") or student_id

    if not student_id or not job_id:
        raise HTTPException(
            status_code=400,
            detail="Missing student_id or job_id"
        )

    job_dir = APP_TMP_DIR / job_id

    email = job.get("Email") or job.get("email")

    try:
        if job_dir.exists():
            pdf_paths = list(job_dir.glob("*report*.pdf"))
            logger.info(pdf_paths)
            if pdf_paths:
                send_report_email(
                    to_email=job.get("Email"),
                    pdf_paths=pdf_paths,
                    student=job,   # ✅ dict
                )
            return {
            "status": "ok",
            "job_id": job_id,
            "pdf_generated": len(pdf_paths),
        }

        job_dir.mkdir(parents=True)

        # guardo el input igual
        with open(job_dir / "input.json", "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)

        # pipeline :3
        logger.info("⚙️ Starting run_student_pipeline")

        pdf_paths, report_types = run_student_pipeline(job, job_dir)

        logger.info(f"📄 Pipeline output report_types={report_types}")
        logger.info(f"📄 Pipeline output pdf_paths={[str(p) for p in pdf_paths]}")

        reports = dict(zip(report_types, pdf_paths))

        # lo guardo en un drive
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
            student_name = (
                job.get("Nombre y Apellido")
                or job.get("Nombre")
                or job.get("nombre")
                or student_id
            )

            safe_name = safe_filename(student_name)
            filename = f"{safe_name}_{report_type}.pdf"

            logger.info(f"⬆️ Uploading to Drive ({report_type})")

            drive_link = upload_pdf_to_drive(
                pdf_path=pdf_path,
                target_folder_id=folder_id,
                filename=filename,
            )

            drive_links[report_type] = drive_link
        
        for report_type, drive_link in drive_links.items():

            post_title = REPORT_TITLES.get(report_type)
            if not post_title:
                continue
            
            try:
                logger.info(f"🌐 Sending {report_type} to SiteGround")
                logger.info(f"Sending the following data: email -> {email}, drive_link -> {drive_link}, post_title -> {post_title}")
                send_report_to_siteground(
                    email=email,
                    drive_link=drive_link,
                    post_title=post_title,
                )
            except Exception:
                logger.exception(
                    f"⚠️ SiteGround failed for {report_type} (Drive link preserved)"
                )

        # solo mandamos el reporte del estudiante al estudiante
        email_pdfs = []

        if "estudiante" in reports:
            email_pdfs.append(reports["estudiante"])

        if "padres" in reports:
            email_pdfs.append(reports["padres"])

        if email_pdfs:
            send_report_email(
                to_email=email,
                pdf_paths=email_pdfs,
                student=job,
            )

        # respuesta exitosa para el AppsScript
        return {
            "status": "ok",
            "job_id": job_id,
            "pdf_generated": report_types,
        }

    except Exception as e:
        logger.exception("💥 Unhandled exception in /run")

        with open(job_dir / "error.txt", "w", encoding="utf-8") as f:
            f.write(str(e))

        raise HTTPException(
            status_code=500,
            detail="Internal error while generating report"
        )
