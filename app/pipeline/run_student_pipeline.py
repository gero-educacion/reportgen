from pathlib import Path
from app.pipeline.data_processing import process_student_data
from app.pipeline.chartgen import generate_graph, generate_graph_utp
from app.pipeline.build_pptx import determine_template, generate_report
from app.pipeline.conversion_pdfs import convert_to_pdf
from app.pipeline.job_config import needs_full_pipeline, chart_style, ASSETS_DIR
# from .email_sender import send_email

def run_student_pipeline(job: dict, job_dir: Path):
    """
    le corremos la pipeline al estudiante de acuerdo a su rol:
        - los del CCR no requieren nada en especial (e.g. procesamiento de datos, charts, etc)
        - los de AC requieren todo so let's go
    """
    rol = job.get("Rol")

    name  = (
        job.get("Nombre y Apellido")
        or job.get("Nombre")
        or job.get("nombre")
        or job.get("nombre_estudiante")
    )

    print("Student's name is ", name)
    print("their role is ", rol)

    # flujo completo para los de AC
    if needs_full_pipeline(rol):
        print("they requiere a complete pipeline")
        student = process_student_data(job, ASSETS_DIR)
        chart_path = job_dir / "chart.png"

        if chart_style(rol) == "utp":
            generate_graph_utp(student, chart_path)
            
        else:
            generate_graph(student, chart_path)

    # flujo de chill para los del CCR
    else:
        print("they require a simple pipeline")
        student = job
        chart_path = None

    # todo el resto se comparte so just do that
    templates = determine_template(student, ASSETS_DIR)
    print("templates determined: ", templates)

    pdf_paths: list[Path] = []
    report_types: list[str] = []

    for suffix, template_path in templates:
        pptx_path = job_dir / f"report_{suffix}.pptx"
        pdf_path  = job_dir / f"report_{suffix}.pdf"

        generate_report(
            student=student,
            pie_chart_path=chart_path,  # porai está vacio
            template_path=template_path,
            output_pptx_path=pptx_path,
        )

        convert_to_pdf(pptx_path, pdf_path)

        pdf_paths.append(pdf_path)
        report_types.append(suffix)

    return pdf_paths, report_types
