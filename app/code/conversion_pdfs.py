from pathlib import Path
import shutil
import subprocess

BASE_DIR = Path(__file__).resolve().parent.parent
pptx_DIR =  BASE_DIR / "outputs"/ "pptx"
output_DIR= BASE_DIR / "outputs"/ "pdf"

import os
import platform
import shutil
import subprocess
from pathlib import Path


def find_soffice() -> str:
    """
    Find a working LibreOffice (soffice) executable.
    Returns the absolute path to soffice.
    Raises RuntimeError if not found or not usable.
    """

    # 1️⃣ Explicit override (BEST option)
    env_path = os.getenv("SOFFICE_PATH")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return str(path)
        raise RuntimeError(f"SOFFICE_PATH is set but invalid: {env_path}")

    # 2️⃣ PATH lookup (may fail)
    path = shutil.which("soffice")
    if path:
        return path

    system = platform.system()

    # 3️⃣ OS-specific known locations
    candidates = []

    if system == "Windows":
        candidates += [
            Path("C:/Program Files/LibreOffice/program/soffice.exe"),
            Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
        ]

    elif system == "Darwin":  # macOS
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

    # 4️⃣ Last resort: try calling libreoffice directly
    for cmd in ("libreoffice", "soffice"):
        try:
            subprocess.run(
                [cmd, "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return cmd
        except Exception:
            pass

    raise RuntimeError(
        "LibreOffice (soffice) not found.\n"
        "Install LibreOffice or set SOFFICE_PATH explicitly."
    )

soffice = find_soffice()

def pptx_to_pdf(pptx_path, output_dir, soffice):
    output_dir.mkdir(parents=True, exist_ok=True)
    print(str(pptx_path))
    subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to", "pdf",
            str(pptx_path),
            "--outdir", str(output_dir),
        ],
        check=True
    )

for archivo in pptx_DIR.iterdir():
    print(archivo)
    print(type(archivo))
    path_archvio =  pptx_DIR/ str(archivo.name)
    if archivo.is_file() and archivo.suffix == ".pptx":
        print(f"Convirtiendo: {archivo.name}")
        pptx_to_pdf(path_archvio, output_DIR, soffice)
