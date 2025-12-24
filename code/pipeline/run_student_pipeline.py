from pathlib import Path
from .data_processing import process_student_data
from .chartgen import generate_graph
from .build_pptx import determine_template, generate_report
from .conversion_pdfs import convert_to_pdf
# from .email_sender import send_email

def run_student_pipeline(job: dict, job_dir: Path) -> Path:
    """
    Runs the full report pipeline for ONE student.
    Returns the final PDF path.
    """
    assets_dir = Path("/app/assets")

    student = process_student_data(job, assets_dir)

    chart_path = job_dir / "chart.png"
    generate_graph(student, chart_path)

    template_path = determine_template(student, assets_dir)

    pptx_path = job_dir / "report.pptx"
    generate_report(
        student=student,
        pie_chart_path=chart_path,
        template_path=template_path,
        output_pptx_path=pptx_path,
    )

    pdf_path = job_dir / "report.pdf"
    convert_to_pdf(pptx_path, pdf_path)

    # send_email(
    #     to=job["email"],
    #     pdf_path=pdf_path,
    #     student=student,
    # )

    return pdf_path
