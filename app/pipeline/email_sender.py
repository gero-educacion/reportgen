import os
import base64
from pathlib import Path
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail,
    Attachment,
    FileContent,
    FileName,
    FileType,
    Disposition,
    Cc,
)
import logging

logger = logging.getLogger(__name__)

CC_ADDRESS = "operaciones@geroeducacion.com"


def get_first_name(student):
    full = (student["Nombre y Apellido"] or student["nombre_estudiante"]).strip()
    return full.split(" ")[0] if full else ""


def build_report_email_html(name: str, site_link: str = "") -> str:
    REPORT_EMAIL_TEMPLATE = f"""
        <div style="font-family:Arial;max-width:520px;margin:0 auto;background:#FFECC7;
            border:3px solid #000;border-radius:20px;padding:20px;
            box-shadow:7px 7px;text-align:center;">

  <img src="https://staging2.geroeducacion.com/wp-content/uploads/2025/10/Logotipo-Gero-Educacion-30.png"
       style="width:180px;height:auto;display:block;margin:0 auto 30px;"
       alt="Gero Educación">

  <h2 style="margin-top:0;">¡Tu reporte ya está listo!</h2>

  <p style="font-size:15px;">
    Hola <strong>{name}</strong>!
  </p>

  <p style="font-size:14px;">
    Te enviamos tu reporte adjunto a este correo para que puedas revisarlo cuando quieras.<br><br>

    Si deseas, también puedes revisarlo en nuestra página web,
    en la sección de <b>"Mis Reportes"</b> 😊.<br><br>

    Saludos,<br>
    El equipo de <b>gero</b>
   </p>

  <p>
    <a href={site_link}
       style="display:inline-block;background:#FF8A00;border:3px solid #000;
              border-radius:12px;color:#111;text-decoration:none;
              padding:12px 18px;font-weight:800;box-shadow:4px 4px 0 #000;">
      Ir a la página
    </a>
  </p>
</div>
"""
    return REPORT_EMAIL_TEMPLATE.format_map({
        "first_name": name,
        "site_link": site_link or "#",
    })


def send_report_email(
    to_email: str,
    pdf_paths: list[Path],
    student: dict,
):
    """
    Sends the report email to the student, with operaciones@ always in CC
    so the team can double-check and reply if needed.
    """
    pdf_paths = [Path(p) for p in pdf_paths]

    if not to_email:
        logger.warning("Skipping email: missing to_email")
        return

    if not pdf_paths:
        logger.warning("Skipping email: empty pdf_paths")
        return

    logger.info(
        f"SENDGRID_API_KEY present={'SENDGRID_API_KEY' in os.environ} "
        f"starts_with={os.environ.get('SENDGRID_API_KEY', '')[:3]}"
    )

    sg = SendGridAPIClient(os.environ.get("SENDGRID_API_KEY"))

    html_content = build_report_email_html(
        name=(student["Nombre y Apellido"] or student["nombre_estudiante"]),
        site_link="https://geroeducacion.com/iniciar-sesion/",
    )

    message = Mail(
        from_email=(os.environ["SENDGRID_FROM"], "Juan de gero"),
        to_emails=to_email,
        subject="Tu reporte - Gero Educación",
        html_content=html_content,
    )
    message.reply_to = "operaciones@geroeducacion.com"

    # CC operaciones so the team always has visibility + can reply
    message.cc = [Cc(CC_ADDRESS)]

    for pdf_path in pdf_paths:
        with open(pdf_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()

        attachment = Attachment(
            FileContent(encoded),
            FileName(pdf_path.name),
            FileType("application/pdf"),
            Disposition("attachment"),
        )
        message.add_attachment(attachment)

    logger.info(f"📧 Sending email to {to_email} CC={CC_ADDRESS} | attachments={len(pdf_paths)}")

    response = sg.send(message)

    if response.status_code >= 400:
        logger.exception("❌ Email sending failed")
    else:
        logger.info("✅ Email sent successfully")