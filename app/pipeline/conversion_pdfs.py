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

    # 1 Explicit override
    env_path = os.getenv("SOFFICE_PATH")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return str(path)
        raise RuntimeError(f"SOFFICE_PATH is set but invalid: {env_path}")

    # 2. PATH lookup
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

    Each call gets its own isolated LibreOffice user-profile directory so
    concurrent conversions (e.g. parallel Cloud Run requests) don't fight
    over the shared ~/.config/libreoffice lock and fail with exit status 1.
    """
    if not pptx_path.exists():
        raise FileNotFoundError(pptx_path)

    soffice = find_soffice()

    output_dir = pdf_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Unique throwaway profile dir — lives next to the output PDF so it's
    # automatically cleaned up when the job_dir is removed.
    profile_dir = output_dir / f".lo_profile_{pptx_path.stem}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    # LibreOffice expects a file:// URI for UserInstallation
    profile_uri = profile_dir.as_uri()

    cmd = [
        soffice,
        "--headless",
        "--nologo",
        "--nodefault",
        "--norestore",
        "--nolockcheck",
        f"-env:UserInstallation={profile_uri}",
        "--convert-to", "pdf:impress_pdf_Export",
        "--outdir", str(output_dir),
        str(pptx_path),
    ]

    try:
        subprocess.run(
            [
                soffice,
                f"-env:UserInstallation={profile_uri}",
                "--headless",
                "--convert-to", "pdf",
                str(pptx_path),
                "--outdir", str(output_dir),
            ],
            check=True,
        )
    finally: 
        shutil.rmtree(profile_dir, ignore_errors=True)

    # LibreOffice outputs PDF with same base name as the input file
    generated_pdf = output_dir / (pptx_path.stem + ".pdf")

    if not generated_pdf.exists():
        raise RuntimeError("LibreOffice did not produce a PDF")

    # Rename/move to the desired pdf_path if needed
    if generated_pdf != pdf_path:
        generated_pdf.replace(pdf_path)

    # Clean up the throwaway profile so we don't accumulate junk
    shutil.rmtree(profile_dir, ignore_errors=True)

    return pdf_path