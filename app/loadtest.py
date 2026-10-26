"""
loadtest.py
-----------
Modo load test del worker (LOADTEST_MODE=1, ver worker.py).

Reemplaza cada efecto externo (Drive, SFTP, CRM de UTP, MySQL, SendGrid,
Sheet de status) por un sleep con la latencia medida en prod, así la carga
sobre lo que sí se mide —cola, workers y Gotenberg (real)— es realista sin
mandar un solo email ni tocar una base real.

Seguridad: en este modo el worker escucha SOLO la cola "loadtest", y los
workers de prod nunca la escuchan. Aunque REDIS_URL apunte a prod por error,
no se procesa ningún job real con mocks, ni ningún job de prueba con efectos reales.

LOADTEST_LATENCY_SCALE  multiplica todas las latencias (default 1.0; 0 = sin espera).
"""
import os
import time
import logging

logger = logging.getLogger("reportgen.loadtest")

LOADTEST_QUEUE = "loadtest"

# Segundos por llamada, de los logs de prod (ya sin el timeout de la Sheet y
# con la conexión MySQL reutilizada).
LATENCY = {
    "drive_json":      3.0,   # upsert_json_to_drive
    "drive_upload":    2.5,   # upload_pdf_to_drive (por PDF, en paralelo)
    "siteground_post": 1.0,   # send_report_to_siteground (por PDF, en paralelo)
    "sftp_upload":     2.5,   # upload_pdf_to_siteground (por PDF, en paralelo)
    "utp_post":        1.6,   # post_utp_payload (POST + UPDATE validationId)
    "mysql_write":     0.3,   # alter_table_reports / write_majors / upsert_historico
    "utp_email":       0.8,   # send_utp_student_email (lookup + SendGrid + UPDATE)
    "email":           1.0,   # send_report_email (SendGrid con adjuntos)
}


def _sleep(kind: str):
    time.sleep(LATENCY[kind] * float(os.environ.get("LOADTEST_LATENCY_SCALE", "1")))


def install_mocks():
    """Parchea app.tasks (y email_sender) en el proceso del worker; los horses lo heredan por fork."""
    import app.tasks as tasks
    import app.pipeline.email_sender as email_sender

    def upload_pdf_to_drive(pdf_path, target_folder_id, filename):
        _sleep("drive_upload"); return f"https://drive.google.com/loadtest/{filename}"

    def upsert_json_to_drive(data, filename, folder_id):
        _sleep("drive_json"); return f"https://drive.google.com/loadtest/{filename}"

    def send_report_to_siteground(email, drive_link, post_title, description=""):
        _sleep("siteground_post"); return {"success": True}

    def upload_pdf_to_siteground(local_path, filename):
        _sleep("sftp_upload"); return f"https://loadtest.invalid/pdf_storage/{filename}"

    def post_utp_payload(*args, **kwargs):
        _sleep("utp_post")

    def mysql_write(*args, **kwargs):
        _sleep("mysql_write")

    def send_utp_student_email(*args, **kwargs):
        _sleep("utp_email")

    def send_report_email(*args, **kwargs):
        _sleep("email")

    def noop(*args, **kwargs):
        return None

    tasks.upload_pdf_to_drive       = upload_pdf_to_drive
    tasks.upsert_json_to_drive      = upsert_json_to_drive
    tasks.send_report_to_siteground = send_report_to_siteground
    tasks.upload_pdf_to_siteground  = upload_pdf_to_siteground
    tasks.post_utp_payload          = post_utp_payload
    tasks.alter_table_reports       = mysql_write
    tasks.write_majors_to_db        = mysql_write
    tasks.upsert_historico          = mysql_write
    tasks.send_report_email         = send_report_email
    tasks.update_student_status     = noop        # la Sheet ya va en batch, fuera del job
    tasks.upsert_student            = noop        # archivo local, sin red
    tasks.get_all_links             = lambda email: {}
    tasks.download_drive_file       = noop
    email_sender.send_utp_student_email = send_utp_student_email  # tasks lo importa adentro de la función

    logger.warning("🧪 LOADTEST_MODE: efectos externos mockeados, Gotenberg real, cola=%s", LOADTEST_QUEUE)
