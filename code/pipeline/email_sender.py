from pathlib import Path
import smtplib
from email.message import EmailMessage


def send_email(to: str, pdf_path: Path, student: dict):
    """
    Sends ONE PDF report to ONE recipient.
    """

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    # ---------------------------
    # Email content
    # ---------------------------
    subject = "Tu reporte de Autoconocimiento :)"
    name = student.get("Nombre y Apellido")

    body = f"""
Hola {name}!

He aquí tu reporte de Autoconocimiento en PDF.
También podrás verlo en la sección de "Mis Reportes" en nuestra página Web!

Saludos,
Equipo de gero
"""
    msg = EmailMessage()
    msg["From"] = "lucia@geroeducacion.com"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    # Attach PDF
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )

    with smtplib.SMTP("smtp.geroeducacion.com", 587) as server:
        server.starttls()
        server.login("lucia@geroeducacion.com", "PASSWORD")
        server.send_message(msg)
