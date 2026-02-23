import logging
from pptx import Presentation
from pathlib import Path

log = logging.getLogger(__name__)

def determine_template(student: dict, assets_dir: Path) -> Path:
    log.info("determining template...")
    rol = student.get("Rol")

    if rol in ("counseling", "compass-directo"):
        template_estudiante = assets_dir / "Template_Autoconocimiento 2.0 (counseling).pptx"
        template_padres = assets_dir / "Template_Autoconocimiento 2.0 (Padres - Counseling).pptx"
        return [
            ("estudiante", template_estudiante), 
            ("padres", template_padres)
        ]
    elif rol == "Rojo":
        template_estudiante = assets_dir / "CCR_EN BOXES.pptx"
        return [
            ("ccr_rojo", template_estudiante)
        ]
    elif rol == "Amarillo":
        template_estudiante = assets_dir / "CCR_CALENTANDO MOTORES.pptx"
        return [
            ("ccr_amarillo", template_estudiante)
        ]
    elif rol == "Verde":
        template_estudiante = assets_dir / "CCR_A TODA MARCHA.pptx"
        return [
            ("ccr_verde", template_estudiante)
        ]
    elif rol == "autoconfianza":
        template_estudiante = assets_dir / "autoconfianza.pptx"
        return [
            ("autoconfianza", template_estudiante)
        ]
    elif rol == "como decidir":
        template_estudiante = assets_dir / "como_decidir.pptx"
        return [
            ("como decidir", template_estudiante)
        ]
    elif rol == "desempatador":
        template_estudiante = assets_dir / "desempatador.pptx"
        return [
            ("desempatador", template_estudiante)
        ]
    else:
        template_estudiante = assets_dir / "Template_Autoconocimiento 2.0 (completo).pptx"
        template_padres = assets_dir / "Template_Autoconocimiento 2.0 (PADRES).pptx"
        return [
            ("estudiante", template_estudiante), 
            ("padres", template_padres)
        ]

def map_placeholders(student: dict) -> dict:
    mapped = {}

    for key, value in student.items():
        placeholder = f"<<{key}>>"
        mapped[placeholder] = value

    return mapped

def generate_report(
    student: dict,
    pie_chart_path: Path,
    template_path: Path,
    output_pptx_path: Path,
):
    log.info("Generating PPTX...")
    prs = Presentation(template_path)
    log.info("The chart exists: ", pie_chart_path)

    placeholders = map_placeholders(student)

    for slide in prs.slides:
        for shape in list(slide.shapes):
            if not shape.has_text_frame:
                continue

            text = shape.text

            for key, val in placeholders.items():

                # IMAGE PLACEHOLDERS
                if isinstance(val, dict) and "image" in val:
                    if key in text:
                        image_path = Path(val["image"])
                        if not image_path.exists():
                            raise FileNotFoundError(image_path)

                        left, top, width, height = (
                            shape.left, shape.top, shape.width, shape.height
                        )

                        shape.text = ""
                        slide.shapes.add_picture(
                            str(image_path),
                            left,
                            top,
                            width=width,
                            height=height
                        )

                # STRING PLACEHOLDERS
                elif isinstance(val, str):
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            if key in run.text:
                                if key == "<<URL GRAFICO HOLLAND>>":
                                    run.text = ""
                                    slide.shapes.add_picture(
                                        str(pie_chart_path),
                                        shape.left,
                                        shape.top,
                                        shape.width,
                                        shape.height
                                    )
                                else:
                                    run.text = run.text.replace(key, val)

                # NUMERIC PLACEHOLDERS
                elif isinstance(val, (int, float)):
                    formatted = f"{round(val * 100, 1)}%"
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            if key in run.text:
                                run.text = run.text.replace(key, formatted)

    # Save PPTX
    output_pptx_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output_pptx_path)

    log.info(f"PPTX written → {output_pptx_path}")
    return output_pptx_path


