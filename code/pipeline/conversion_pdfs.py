from pathlib import Path
import subprocess
import shutil
import os
import platform

def find_soffice() -> str:
    """
    Find a working LibreOffice (soffice) executable.
    Returns the executable path or name.
    Raises RuntimeError if not found.
    """

    # 1️⃣ Explicit override
    env_path = os.getenv("SOFFICE_PATH")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return str(path)
        raise RuntimeError(f"SOFFICE_PATH is set but invalid: {env_path}")

    # 2️⃣ PATH lookup
    path = shutil.which("soffice")
    if path:
        return path

    system = platform.system()
    candidates = []

    if system == "Windows":
        candidates += [
            Path("C:/Program Files/LibreOffice/program/soffice.exe"),
            Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
        ]
    elif system == "Darwin":
        candidates += [
            Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        ]
    elif system == "Linux":
        candidates += [
            Path("/usr/bin/soffice"),
            Path("/usr/lib/libreoffice/program/soffice"),
            Path("/snap/bin/libreoffice"),
        ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        "LibreOffice (soffice) not found. "
        "Install LibreOffice or set SOFFICE_PATH."
    )


def convert_to_pdf(pptx_path: Path, pdf_path: Path) -> Path:
    """
    Converts ONE PPTX file to ONE PDF file using LibreOffice.
    """

    if not pptx_path.exists():
        raise FileNotFoundError(pptx_path)

    soffice = find_soffice()

    output_dir = pdf_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to", "pdf",
            str(pptx_path),
            "--outdir", str(output_dir),
        ],
        check=True,
    )

    # LibreOffice outputs PDF with same base name
    generated_pdf = output_dir / (pptx_path.stem + ".pdf")

    if not generated_pdf.exists():
        raise RuntimeError("LibreOffice did not produce a PDF")

    # Rename/move to desired pdf_path if needed
    if generated_pdf != pdf_path:
        generated_pdf.replace(pdf_path)

    return pdf_path
