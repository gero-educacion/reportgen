from pathlib import Path
import subprocess
import shutil
import os
import platform
import threading
import uuid
import logging

logger = logging.getLogger(__name__)

CONVERSION_TIMEOUT = int(os.environ.get("LO_TIMEOUT", 120))  # seconds per conversion
_CONVERSION_SEMAPHORE = threading.Semaphore(1)  # one soffice at a time


def find_soffice() -> str:
    """
    Find a working LibreOffice (soffice) executable.
    Returns the executable path or name.
    Raises RuntimeError if not found.
    """
    # 1. Explicit override
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
    if not pptx_path.exists():
        raise FileNotFoundError(pptx_path)

    soffice = find_soffice()

    output_dir = pdf_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Unique profile dir per conversion — avoids collisions between concurrent jobs
    # and between a retry and a still-running (or crashed) previous attempt.
    profile_dir = output_dir / f".lo_profile_{pptx_path.stem}_{uuid.uuid4().hex}"
    profile_dir.mkdir(parents=True, exist_ok=True)
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

    with _CONVERSION_SEMAPHORE:
        proc = None
        try:
            logger.info("🖨️  Starting LibreOffice conversion: %s (timeout=%ds)", pptx_path.name, CONVERSION_TIMEOUT)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = proc.communicate(timeout=CONVERSION_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()  # drain pipes to avoid zombie
                raise RuntimeError(
                    f"LibreOffice timed out after {CONVERSION_TIMEOUT}s converting {pptx_path.name}"
                )

            if proc.returncode != 0:
                raise RuntimeError(
                    f"LibreOffice failed (exit {proc.returncode})\n"
                    f"stdout: {stdout}\n"
                    f"stderr: {stderr}"
                )

            logger.info("✅ LibreOffice finished: %s", pptx_path.name)

        finally:
            # Kill any still-running process (safety net for unexpected paths)
            if proc and proc.poll() is None:
                proc.kill()
            shutil.rmtree(profile_dir, ignore_errors=True)

    generated_pdf = output_dir / (pptx_path.stem + ".pdf")

    if not generated_pdf.exists():
        raise RuntimeError(f"LibreOffice did not produce a PDF for {pptx_path.name}")

    if generated_pdf != pdf_path:
        generated_pdf.replace(pdf_path)

    return pdf_path