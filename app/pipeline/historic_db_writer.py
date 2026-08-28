"""
historic_db_writer.py
---------------------
Upserts one student's processed payload into byw_autoconocimiento_historico.

Ported from app.py::build_historico_row() — same field mapping, same logic —
but writes directly to MySQL via PyMySQL instead of going through the CSV →
upload.php route.

Call `upsert_historico(job)` right after run_student_pipeline() succeeds.
Non-fatal: any exception is logged and swallowed so the rest of the pipeline
is never blocked.

Required env vars (shared with db_writer.py):
  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
"""

import re
import logging
from datetime import datetime

from app.pipeline.db import get_connection as _get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapping tables  (copied verbatim from app.py so they stay in sync)
# ---------------------------------------------------------------------------

HOLLAND_KEY_TO_COL = {
    "EMPRENDEDOR":  "holland_emprendedor",
    "SOCIAL":       "holland_social",
    "INVESTIGATIVO":"holland_investigativo",
    "CONVENCIONAL": "holland_convencional",
    "ARTÍSTICO":    "holland_artistico",
    "REALISTA":     "holland_realista",
}
HOLLAND_PRESENCIA_KEY_TO_COL = {
    "EMPRENDEDOR":  "holland_presencia_emprendedor",
    "SOCIAL":       "holland_presencia_social",
    "INVESTIGATIVO":"holland_presencia_investigativo",
    "CONVENCIONAL": "holland_presencia_convencional",
    "ARTÍSTICO":    "holland_presencia_artistico",
    "REALISTA":     "holland_presencia_realista",
}
GARDNER_KEY_TO_COL = {
    "LINGÜÍSTICO-VERBAL":     "intel_linguistico_verbal",
    "INTERPERSONAL":          "intel_interpersonal",
    "INTRAPERSONAL":          "intel_intrapersonal",
    "LÓGICO-MATEMÁTICA":      "intel_logico_matematica",
    "VISUAL-ESPACIAL":        "intel_visual_espacial",
    "NATURALISTA":            "intel_naturalista",
    "CORPORAL-KINESTÉSICA":   "intel_corporal_kinestesica",
    "MUSICAL":                "intel_musical",
}
AE_POSITION_TO_COL = {n: f"pct_ae_{n}" for n in range(1, 9)}

MAX_ARQ_MAP = {
    "✔️ el hacer práctico, el movimiento y la resolución de problemas con tus propias manos. Te atrae experimentar con tecnología de forma concreta y participar en proyectos donde el resultado sea visible y tangible.": "REALISTA",
    "✔️ la curiosidad intelectual, el análisis y la búsqueda de explicaciones profundas. Te interesa descubrir cómo funciona el mundo, explorar ideas nuevas y resolver problemas con pensamiento crítico.": "INVESTIGATIVO",
    "✔️ la creatividad, la expresión personal y la exploración de ideas originales. Te motiva crear, comunicar y transformar la realidad a través del arte, la imaginación o nuevas formas de representación.": "ARTÍSTICO",
    "✔️ las relaciones humanas, la empatía y el trabajo colaborativo. Te motiva conectar con otras personas, apoyar a tu entorno y participar en acciones que generen impacto positivo.": "SOCIAL",
    "✔️ la organización, la estructura y la gestión eficiente. Te motiva planificar, optimizar recursos y mantener el control sobre los procesos para que todo funcione de forma ordenada.": "CONVENCIONAL",
    "✔️ el liderazgo, la iniciativa y la búsqueda de resultados concretos. Te motiva emprender proyectos, asumir responsabilidades y generar impacto a través de la acción estratégica.": "EMPRENDEDOR",
}

def _norm(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE).lower()

MAX_ARQ_MAP_NORM = {_norm(k): v for k, v in MAX_ARQ_MAP.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(val):
    if val is None:
        return None
    s = str(val).strip()
    return None if s == "" else float(s)

def _upper(val):
    if val is None:
        return None
    s = str(val).strip()
    return s.upper() if s else None

def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip().lstrip("✔️").strip()
        if line:
            return line[:255]
    return ""


# ---------------------------------------------------------------------------
# Row builder  (mirrors app.py::build_historico_row exactly)
# ---------------------------------------------------------------------------

def _build_row(student: dict) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = {
        "email":  (student.get("Email") or "").strip().lower(),
        "nombre": (student.get("Nombre y Apellido") or "").strip(),
        "rol":    (student.get("Rol") or "").strip(),
        "pais":   (student.get("Pais") or "").strip(),
        "rec_fortalezas_1": _first_line(student.get("Carrera 01", "")),
        "rec_fortalezas_2": _first_line(student.get("Carrera 02", "")),
        "rec_intereses_1":  (student.get("Carrera 03") or "").strip(),
        "rec_intereses_2":  (student.get("Carrera 04") or "").strip(),
        "ARQ_estilo_de_vida": (student.get("ARQ Estilo") or "").strip().upper(),
    }

    # Holland percentages + presencia
    holland_pct, holland_presencia = {}, {}
    for n in range(1, 7):
        name  = (student.get(f"Holland {n:02d}") or "").strip().upper()
        level = (student.get(f"PRE Holland {n:02d}") or "").strip().upper()
        pct   = student.get(f"% Holland {n:02d}")
        if name and pct is not None:
            holland_pct[name] = _to_float(pct)
        if name and level:
            holland_presencia[name] = level

    for label, col in HOLLAND_KEY_TO_COL.items():
        row[col] = holland_pct.get(label, 0.0)
    for label, col in HOLLAND_PRESENCIA_KEY_TO_COL.items():
        row[col] = holland_presencia.get(label)

    # Áreas de estudio
    for n in range(1, 9):
        row[AE_POSITION_TO_COL[n]] = _to_float(student.get(f"% AE {n:02d}"))

    # Gardner
    gardner_level = {}
    for n in range(1, 9):
        name  = (student.get(f"Gardner {n:02d}") or "").strip().upper()
        level = (student.get(f"% Gardner {n:02d}") or "").strip().upper()
        if name and level:
            gardner_level[name] = level
    for label, col in GARDNER_KEY_TO_COL.items():
        row[col] = gardner_level.get(label)

    # Niveles + ARQ intereses
    row.update({
        "nivel_abstracto":              _upper(student.get("Nivel Abstracto")),
        "nivel_numerico":               _upper(student.get("Nivel Numérico")),
        "nivel_verbal":                 _upper(student.get("Nivel Verbal")),
        "nivel_gestion_tiempo":         _upper(student.get("Nivel Gestion Tiempo")),
        "nivel_habitos_estudio":        _upper(student.get("Nivel Habitos de estudio")),
        "nivel_inteligencia_emocional": _upper(student.get("Nivel Inteligencia Emocional")),
        "nivel_perseverancia":          _upper(student.get("Nivel Perseverancia")),
        "arq_intereses_1": MAX_ARQ_MAP_NORM.get(_norm(student.get("DESC interes 01", ""))),
        "arq_intereses_2": MAX_ARQ_MAP_NORM.get(_norm(student.get("DESC interes 02", ""))),
        "created_at": now,
        "updated_at": now,
    })

    return row


# Columns upload.php allows — keeps us in sync with the PHP whitelist.
# created_at / updated_at are handled separately in the SQL below.
ALLOWED_COLS = {
    "email", "nombre", "rol", "pais",
    "rec_fortalezas_1", "rec_fortalezas_2", "rec_intereses_1", "rec_intereses_2",
    "ARQ_estilo_de_vida",
    "holland_presencia_realista", "holland_presencia_investigativo",
    "holland_presencia_artistico", "holland_presencia_social",
    "holland_presencia_emprendedor", "holland_presencia_convencional",
    "holland_realista", "holland_investigativo", "holland_artistico",
    "holland_social", "holland_emprendedor", "holland_convencional",
    "pct_ae_1", "pct_ae_2", "pct_ae_3", "pct_ae_4",
    "pct_ae_5", "pct_ae_6", "pct_ae_7", "pct_ae_8",
    "intel_interpersonal", "intel_linguistico_verbal",
    "intel_corporal_kinestesica", "intel_musical",
    "intel_visual_espacial", "intel_intrapersonal",
    "intel_logico_matematica", "intel_naturalista",
    "nivel_abstracto", "nivel_numerico", "nivel_verbal", "nivel_gestion_tiempo",
    "nivel_habitos_estudio", "nivel_inteligencia_emocional", "nivel_perseverancia",
    "arq_intereses_1", "arq_intereses_2",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def upsert_historico(student: dict):
    """
    Maps `student` (the raw job payload) to byw_autoconocimiento_historico
    and upserts it.  Non-fatal — logs and returns on any error.

    Call this right after run_student_pipeline() succeeds, e.g.:

        pdf_paths, report_types = run_student_pipeline(job, job_dir)
        upsert_historico(job)
    """
    email = (student.get("Email") or student.get("email") or "").strip()
    if not email:
        logger.warning("upsert_historico: no email in payload, skipping")
        return

    try:
        row = _build_row(student)

        # Keep only whitelisted columns, and remap pct_ae_{n:02d} → pct_ae_{n}
        # (_build_row uses {n} already via AE_POSITION_TO_COL; no remap needed)
        data = {k: v for k, v in row.items() if k in ALLOWED_COLS and v is not None}

        if not data:
            logger.warning("upsert_historico: nothing to write for %s", email)
            return

        cols         = list(data.keys())
        col_list     = ", ".join(f"`{c}`" for c in cols)
        placeholders = ", ".join(f"%({c})s" for c in cols)
        update_parts = ", ".join(
            f"`{c}` = VALUES(`{c}`)"
            for c in cols if c != "email"
        )

        sql = f"""
            INSERT INTO byw_autoconocimiento_historico
                ({col_list}, `created_at`, `updated_at`)
            VALUES
                ({placeholders}, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                {update_parts},
                `updated_at` = NOW()
        """

        conn = _get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, data)
            conn.commit()

        logger.info("✅ byw_autoconocimiento_historico upserted for %s", email)

    except Exception:
        logger.exception("⚠️  upsert_historico failed for %s (non-fatal)", email)