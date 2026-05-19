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
    Bcc,
)
import logging

logger = logging.getLogger(__name__)

CC_ADDRESS = "operaciones@geroeducacion.com"


def get_first_name(student):
    full = (student.get("Nombre y Apellido") or student.get("nombre_estudiante")).strip()
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
        name=(student.get("Nombre y Apellido") or student.get("nombre_estudiante")),
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

def _get_student_contact(cedula: str) -> dict | None:
    """
    Looks up nombre, apellido and email from byw_usuarios_habilitados
    matching cedula_matricula = cedula.
    Returns a dict with keys nombre, apellido, email or None if not found.
    """
    import pymysql
    import pymysql.cursors

    sql = """
        SELECT nombre, apellido, email
        FROM byw_usuarios_habilitados
        WHERE LOWER(cedula_matricula) = LOWER(%s)
          AND cliente = 'Universidad Tecnológica de Perú'
        LIMIT 1
    """
    try:
        conn = pymysql.connect(
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", 3306)),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            database=os.environ["DB_NAME"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, (cedula,))
                return cur.fetchone()
    except Exception:
        logger.exception("Failed to fetch student contact for cedula=%s", cedula)
        return None


def build_utp_student_email_html(nombre: str, apellido: str, reporte_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tu Perfil Vocacional</title>
</head>
<body style="margin:0; padding:0; background-color:#f4f4f4; font-family: Calibri, 'Calibri', sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4; padding: 30px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border-radius:4px; overflow:hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
          <tr>
            <td style="background-color:#ffffff; padding: 28px 40px;">
              <img src="https://geroeducacion.com/wp-content/uploads/2026/03/UTP_LOGO_BRANDBOOK-copia-2.png" alt="UTP Logo" style="height: 60px; max-width: 260px;" />
            </td>
          </tr>
          <tr>
            <td style="height: 5px; background-color: #C8102E;"></td>
          </tr>
          <tr>
            <td style="padding: 40px 40px 30px 40px;">
              <p style="font-family: Calibri, sans-serif; font-size: 16px; color: #222222; line-height: 1.6; margin: 0 0 18px 0;">
                ¡Hola <strong>{nombre} {apellido}</strong>!
              </p>
              <p style="font-family: Calibri, sans-serif; font-size: 16px; color: #222222; line-height: 1.6; margin: 0 0 18px 0;">
                Muchas gracias por realizar tu orientación vocacional junto con la Universidad Tecnológica del Perú.
              </p>
              <p style="font-family: Calibri, sans-serif; font-size: 16px; color: #222222; line-height: 1.6; margin: 0 0 28px 0;">
                Te compartimos en el siguiente link tu perfil vocacional.
              </p>
              <table cellpadding="0" cellspacing="0" style="margin-bottom: 30px;">
                <tr>
                  <td style="background-color: #C8102E; border-radius: 4px;">
                    <a href="{reporte_url}" style="display: inline-block; padding: 13px 32px; font-family: Calibri, sans-serif; font-size: 15px; font-weight: bold; color: #ffffff; text-decoration: none; letter-spacing: 0.3px;">
                      Ver mi perfil vocacional &rarr;
                    </a>
                  </td>
                </tr>
              </table>
              <p style="font-family: Calibri, sans-serif; font-size: 16px; color: #222222; line-height: 1.6; margin: 0 0 6px 0;">
                Cualquier duda cuentas con nosotros para acompañarte.
              </p>
              <p style="font-family: Calibri, sans-serif; font-size: 16px; color: #222222; line-height: 1.6; margin: 0 0 30px 0;">
                Saludos,
              </p>
            </td>
          </tr>
          <tr>
            <td style="background-color: #1a1a1a; padding: 20px 40px;">
              <p style="font-family: Calibri, sans-serif; font-size: 13px; color: #999999; margin: 0; text-align: center;">
                Universidad Tecnológica del Perú &nbsp;|&nbsp; utp.edu.pe
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_utp_student_email(cedula: str, reporte_url: str) -> None:
    """
    Sends the UTP student report email.
    Looks up nombre, apellido and email from byw_usuarios_habilitados by cedula_matricula.
    """
    if not reporte_url:
        logger.warning("send_utp_student_email: missing reporte_url, skipping")
        return

    contact = _get_student_contact(cedula)
    if not contact:
        logger.warning("send_utp_student_email: no contact found for cedula=%s", cedula)
        return

    nombre = contact["nombre"]
    apellido = contact["apellido"]
    to_email = contact["email"]

    if not to_email:
        logger.warning("send_utp_student_email: no email for cedula=%s", cedula)
        return

    sg = SendGridAPIClient(os.environ.get("SENDGRID_API_KEY"))

    message = Mail(
        from_email=os.environ["SENDGRID_FROM"],
        to_emails=to_email,
        subject='🔴 UTP | Tu perfil vocacional está listo',
        html_content=build_utp_student_email_html(nombre, apellido, reporte_url),
    )
    message.reply_to = CC_ADDRESS
    message.bcc = [Bcc(CC_ADDRESS)]

    logger.info("📧 Sending UTP student email to %s for cedula=%s", to_email, cedula)

    try:
        response = sg.send(message)
        if response.status_code >= 400:
          logger.error("❌ UTP student email failed — status %s", response.status_code)
        else:
          logger.info("✅ UTP student email sent to %s", to_email)
    except Exception as e:
        logger.error("❌ UTP student email error — %s | body=%s", e, getattr(e, 'body', None))
        raise
    
