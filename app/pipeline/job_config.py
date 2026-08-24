"""
job_config.py
-------------
Single source of truth for everything that varies by student "Rol".

Adding a new role/job type = adding ONE entry to ROLE_CONFIGS.
No more touching build_pptx.py's determine_template(), tasks.py's
REPORT_TITLES / REPORT_FILENAMES / UTP_ROLES / the drive-folder if-chain,
or run_student_pipeline.py's PIPELINE_COMPLETO — they all read from here.

Schema per role:
{
    "pipeline":     "chill" | "full" | "utp_chart",
        # chill      -> no data_processing/chart, raw `job` dict used as-is (like CCR)
        # full       -> process_student_data() + generate_graph() (standard AC style)
        # utp_chart  -> process_student_data() + generate_graph_utp() (flat UTP style)
    "is_utp":       bool,   # True -> SFTP upload + CRM POST branch in tasks.py
                            #         instead of Drive + WP-notify branch
    "write_majors": bool,   # True -> write_majors_to_db() runs for this role
    "reports": [
        {
            "suffix":           str,   # e.g. "estudiante" — MUST be unique per role,
                                        # used as dict key everywhere downstream
            "template":         str,   # filename only, resolved under ASSETS_DIR
            "title":            str,   # used for SiteGround CMS post title
            "filename":         str,   # used for historic-resend download naming
            "drive_folder_env": str,   # env var name holding the Drive folder ID
        },
        ...
    ],
}

Anything not found in ROLE_CONFIGS falls back to "_default" (the plain
Autoconocimiento estudiante/padres pair), matching the old `else` branch
in determine_template().

si encontras este comentario y lo estas leyendo te quiero mucho ojala tu vida sea muy hermosa y si no lo es lo será.
"""

import os
from pathlib import Path

ASSETS_DIR = Path("/app/assets")


ROLE_CONFIGS: dict = {

    "counseling": {
        "pipeline": "full",
        "is_utp": False,
        "write_majors": True,
        "reports": [
            {
                "suffix": "estudiante",
                "template": "Template_Autoconocimiento 2.0 (counseling).pptx",
                "title": 'Reporte "Autoconocimiento"',
                "filename": "Reporte_Autoconocimiento.pdf",
                "drive_folder_env": "DRIVE_FOLDER_AUTO_EST",
                "description": "Tu Guía de Orientación Vocacional, incluye información sobre tu personalidad, intereses, habilidades e hipótesis de carrera que te recomendamos explorar."
            },
            {
                "suffix": "padres",
                "template": "Template_Autoconocimiento 2.0 (Padres - Counseling).pptx",
                "title": 'Reporte "Autoconocimiento versión padres"',
                "filename": "Reporte_Autoconocimiento_Padres.pdf",
                "drive_folder_env": "DRIVE_FOLDER_AUTO_PAD",
                "description": "Los resultados obtenidos en autoconocimiento para que puedas compartirlo con tus padres. No te preocupes, la información es la misma que recibiste tú!"
            },
        ],
    },

    "compass-directo": {
        "pipeline": "full",
        "is_utp": False,
        "write_majors": True,
        "reports": [
            {
                "suffix": "estudiante",
                "template": "Template_Autoconocimiento 2.0 (counseling).pptx",
                "title": 'Reporte "Autoconocimiento"',
                "filename": "Reporte_Autoconocimiento.pdf",
                "drive_folder_env": "DRIVE_FOLDER_AUTO_EST",
                "description": "Tu Guía de Orientación Vocacional, incluye información sobre tu personalidad, intereses, habilidades e hipótesis de carrera que te recomendamos explorar."
            },
            {
                "suffix": "padres",
                "template": "Template_Autoconocimiento 2.0 (Padres - Counseling).pptx",
                "title": 'Reporte "Autoconocimiento versión padres"',
                "filename": "Reporte_Autoconocimiento_Padres.pdf",
                "drive_folder_env": "DRIVE_FOLDER_AUTO_PAD",
                "description": "Los resultados obtenidos en autoconocimiento para que puedas compartirlo con tus padres. No te preocupes, la información es la misma que recibiste tú!"
            },
        ],
    },

    "Rojo": {
        "pipeline": "chill",
        "is_utp": False,
        "write_majors": False,
        "reports": [
            {
                "suffix": "ccr_rojo",
                "template": "CCR_EN BOXES.pptx",
                "title": "¿Cuán Preparado/a Estás?",
                "filename": "CCR_En_Boxes.pdf",
                "drive_folder_env": "DRIVE_FOLDER_CCR",
                "description": "Un diagnóstico sobre tu estado actual respecto a cuán preparado/a estás para tomar una decisión de carrera madura y sugerencias de por dónde continuar investigando."
            },
        ],
    },

    "Amarillo": {
        "pipeline": "chill",
        "is_utp": False,
        "write_majors": False,
        "reports": [
            {
                "suffix": "ccr_amarillo",
                "template": "CCR_CALENTANDO MOTORES.pptx",
                "title": "¿Cuán Preparado/a Estás?",
                "filename": "CCR_Calentando_Motores.pdf",
                "drive_folder_env": "DRIVE_FOLDER_CCR",
                "description": "Un diagnóstico sobre tu estado actual respecto a cuán preparado/a estás para tomar una decisión de carrera madura y sugerencias de por dónde continuar investigando."               
            },
        ],
    },

    "Verde": {
        "pipeline": "chill",
        "is_utp": False,
        "write_majors": False,
        "reports": [
            {
                "suffix": "ccr_verde",
                "template": "CCR_A TODA MARCHA.pptx",
                "title": "¿Cuán Preparado/a Estás?",
                "filename": "CCR_A_Toda_Marcha.pdf",
                "drive_folder_env": "DRIVE_FOLDER_CCR",
                "description": "Un diagnóstico sobre tu estado actual respecto a cuán preparado/a estás para tomar una decisión de carrera madura y sugerencias de por dónde continuar investigando."
            },
        ],
    },

    "UTP": {
        "pipeline": "utp_chart",
        "is_utp": True,
        "write_majors": False,   # UTP already goes through post_utp_payload instead
        "reports": [
            {
                "suffix": "estudiante",
                "template": "template_utp_alumnos.pptx",
                "title": "Reporte UTP Estudiante",
                "filename": "Reporte_UTP_Estudiante.pdf",
                "drive_folder_env": "",   # unused — UTP uploads via SFTP, not Drive
                "description": ""
            },
            {
                "suffix": "padres",
                "template": "template_utp_padres.pptx",
                "title": "Reporte UTP Padres",
                "filename": "Reporte_UTP_Padres.pdf",
                "drive_folder_env": "",
                "description": ""
            },
        ],
    },

    # TU CONTEXTO
    "contexto": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "contexto",
                "template": "template_tu_contexto.pptx",
                "title": 'Reporte "Tu contexto"',
                "filename": "Reporte_tu_contexto.pdf",
                "drive_folder_env": "DRIVE_FOLDER_CONTEXTO",
                "description": "Un reporte sobre mitos y verdades en el proyecto de vida y las respuestas sobre la mirada sobre ti mismo/a y la de tu familia."
            }
        ],
    },

    # TU PROPÓSITO
    "proposito": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "proposito",
                "template": "template_tu_proposito.pptx",
                "title": 'Reporte "Tu propósito"',
                "filename": "Reporte_tu_proposito.pdf",
                "drive_folder_env": "DRIVE_FOLDER_PROPOSITO",
                "description": "Una compilacion de tus respuestas a tu mirada de la vida, del trabajo y tu propósito para que acompañe tu proceso."
            }
        ],
    },

    # Autoconfianza
    "autoconfianza": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "autoconfianza",
                "template": "template_autoconfianza.pptx",
                "title": 'Reporte "Autoconfianza"',
                "filename": "Reporte_autoconfianza.pdf",
                "drive_folder_env": "DRIVE_FOLDER_AUTOCONFIANZA",
                "description": "Reporte sobre cómo te comportas frente a los desafíos, aspectos que te benefician y sugerencias para atravesarlos."
            }
        ],
    },

    # Desempatador de carreras
    "desempatador": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "desempatador",
                "template": "template_desempatador.pptx",
                "title": 'Desempatador de carreras',
                "filename": "Desempatador_carreras.pdf",
                "drive_folder_env": "DRIVE_FOLDER_DESEMPATADOR",
                "description": "Conoce el porcentaje de cercanía hacía tus metas y estilo de vida de cada carrera de interés."
            }
        ],
    },

    "que_estudiar": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "que_estudiar",
                "template": "template_que_estudiar.pptx",
                "title": 'Reporte "Qué estudiar"',
                "filename": "Reporte_que_estudiar.pdf",
                "drive_folder_env": "DRIVE_FOLDER_QUE_ESTUDIAR",
                "description": "Una compilación de las carreras exploradas con lo que más te ha gustado de cada una y los resultados de tu propio prototipo."
            }
        ],
    },

    "donde_estudiar": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "donde_estudiar",
                "template": "template_donde_estudiar.pptx",
                "title": 'Reporte "Dónde estudiar"',
                "filename": "Reporte_donde_estudiar.pdf",
                "drive_folder_env": "DRIVE_FOLDER_DONDE_ESTUDIAR",
                "description": "Una compilación de las instituciones exploradas con lo que has aprendido y lo que más te ha gustado de cada una de ellas."
            }
        ],
    },

    "como_decidir": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "como_decidir",
                "template": "template_como_decidir.pptx",
                "title": 'Reporte "Cómo Decidir"',
                "filename": "Reporte_como_decidir.pdf",
                "drive_folder_env": "DRIVE_FOLDER_COMO_DECIDIR",
                "description": "Un reporte sobre tu decisión de próximo paso académico."
            }
        ],
    },

    "lupita": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "lupita",
                "template": "template_lupita.pptx",
                "title": 'Diseña tu lupita',
                "filename": "tu_lupita.pdf",
                "drive_folder_env": "DRIVE_FOLDER_LUPITA",
                "description": 'Este reporte contiene los resultados de tus respuestas a los cuestionarios de "diseña tu lupita".'
            }
        ],
    },

    "islas": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "islas",
                "template": "template_islas_identidad.pptx",
                "title": 'Islas de Identidad',
                "filename": "islas_identidad.pdf",
                "drive_folder_env": "DRIVE_FOLDER_ISLAS",
                "description": 'Este reporte contiene los resultados de tus respuestas a la actividad de "islas de identidad"'
            }
        ],
    },

    "mi_identidad": {
        "pipeline": "chill",       
        "is_utp": False,
        "write_majors": False,     
        "reports": [
            {
                "suffix": "mi_identidad",
                "template": "template_mi_identidad.pptx",
                "title": 'Mi identidad',
                "filename": "mi_identidad.pdf",
                "drive_folder_env": "DRIVE_FOLDER_MI_IDENTIDAD",
                "description": 'Este reporte contiene los resultados de tus respuestas a la actividad de "Desafíos VS yo"'
            }
        ],
    },

    "_default": {
        "pipeline": "full",
        "is_utp": False,
        "write_majors": True,
        "reports": [
            {
                "suffix": "estudiante",
                "template": "Template_Autoconocimiento 2.0 (completo).pptx",
                "title": 'Reporte "Autoconocimiento"',
                "filename": "Reporte_Autoconocimiento.pdf",
                "drive_folder_env": "DRIVE_FOLDER_AUTO_EST",
            },
            {
                "suffix": "padres",
                "template": "Template_Autoconocimiento 2.0 (PADRES).pptx",
                "title": 'Reporte "Autoconocimiento versión padres"',
                "filename": "Reporte_Autoconocimiento_Padres.pdf",
                "drive_folder_env": "DRIVE_FOLDER_AUTO_PAD",
            },
        ],
    },
}


def get_role_config(rol: str) -> dict:
    return ROLE_CONFIGS.get(rol, ROLE_CONFIGS["_default"])


def get_templates(rol: str) -> list[tuple[str, Path]]:
    """Replaces build_pptx.determine_template()'s return value."""
    cfg = get_role_config(rol)
    return [(r["suffix"], ASSETS_DIR / r["template"]) for r in cfg["reports"]]


def get_report_titles(rol: str) -> dict[str, str]:
    """Replaces the global REPORT_TITLES dict, scoped per role."""
    return {r["suffix"]: r["title"] for r in get_role_config(rol)["reports"]}


def get_report_filenames(rol: str) -> dict[str, str]:
    """Replaces the global REPORT_FILENAMES dict, scoped per role."""
    return {r["suffix"]: r["filename"] for r in get_role_config(rol)["reports"]}


def get_drive_folder(rol: str, suffix: str) -> str | None:
    """Replaces the if/elif/else drive-folder chain in tasks.py."""
    for r in get_role_config(rol)["reports"]:
        if r["suffix"] == suffix:
            env_name = r.get("drive_folder_env")
            return os.environ.get(env_name) if env_name else None
    return None


def needs_full_pipeline(rol: str) -> bool:
    return get_role_config(rol)["pipeline"] in ("full", "utp_chart")


def chart_style(rol: str) -> str | None:
    """Returns 'standard', 'utp', or None (no chart needed)."""
    pipeline = get_role_config(rol)["pipeline"]
    if pipeline == "full":
        return "standard"
    if pipeline == "utp_chart":
        return "utp"
    return None


def is_utp_role(rol: str) -> bool:
    return get_role_config(rol)["is_utp"]


def should_write_majors(rol: str) -> bool:
    return get_role_config(rol)["write_majors"]

def get_report_description(rol: str, suffix: str) -> str:
    for r in get_role_config(rol)["reports"]:
        if r["suffix"] == suffix:
            return r.get("description", "")
    return ""

def role_is_ready(rol: str) -> tuple[bool, list[str]]:
    """Checks templates exist + required env vars are set for this role."""
    problems = []
    if rol == "":
        problems.append(f"No role found")
    for r in get_role_config(rol)["reports"]:
        if not (ASSETS_DIR / r["template"]).exists():
            problems.append(f"template not found: {r['template']}")
        env_name = r.get("drive_folder_env")
        if env_name and not os.environ.get(env_name):
            problems.append(f"env var {env_name} not set")
    return (len(problems) == 0, problems)

def get_all_report_filenames() -> dict[str, str]:
    """
    Flat suffix -> filename map across EVERY role. Used only by the
    cross-role historic-resend path in tasks.py, since one student's
    Drive history can span multiple roles taken at different times —
    unlike everywhere else, which only ever needs the current role's types.
    """
    result: dict[str, str] = {}
    for cfg in ROLE_CONFIGS.values():
        for r in cfg["reports"]:
            result[r["suffix"]] = r["filename"]
    return result
