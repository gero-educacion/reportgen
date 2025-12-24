from pathlib import Path
import copy


# ---------------------------
# IMAGE MAPS (FLAT ASSETS)
# ---------------------------

def _presence_image(level: str, assets_dir: Path) -> dict:
    level = (level or "").upper()

    if level == "ALTA":
        return {"image": str(assets_dir / "Presencia_ALTA.png")}
    elif level == "MEDIA":
        return {"image": str(assets_dir / "Presencia_MEDIA.png")}
    else:
        return {"image": str(assets_dir / "Presencia_BAJA.png")}


def _ae_bar_image(value: float, assets_dir: Path) -> dict:
    thresholds = [
        (0.95, 100),
        (0.85, 90),
        (0.75, 80),
        (0.65, 70),
        (0.55, 60),
        (0.45, 50),
        (0.35, 40),
        (0.25, 30),
        (0.15, 20),
        (0.05, 10),
    ]

    for limit, pct in thresholds:
        if value >= limit:
            return {"image": str(assets_dir / f"Progreso_{pct}%.png")}

    return {"image": str(assets_dir / "Progreso_0%.png")}


# ---------------------------
# MAIN TRANSFORM
# ---------------------------

def process_student_data(student: dict, assets_dir: Path) -> dict:
    """
    Enriches ONE student dict with image placeholders.
    Returns a NEW dict (does not mutate input).
    """

    result = copy.deepcopy(student)

    for i in range(1, 9):

        # ---------- GARDNER ----------
        g_key = f"% Gardner 0{i}"
        g_url_key = f"URL Gardner 0{i}"

        level = result.get(g_key)
        if level:
            result[g_url_key] = _presence_image(level, assets_dir)

        # ---------- ÁREAS DE ESTUDIO ----------
        ae_key = f"% AE 0{i}"
        ae_url_key = f"URL AE 0{i}"

        value = result.get(ae_key)

        if value in ("", None):
            result[ae_url_key] = ""
            break

        try:
            value = float(value)
        except (TypeError, ValueError):
            result[ae_url_key] = ""
            continue

        result[ae_url_key] = _ae_bar_image(value, assets_dir)

    return result
