from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from datetime import datetime
from pathlib import Path
import json
from pipeline.run_student_pipeline import run_student_pipeline

app = FastAPI()

BASE_JOB_DIR = Path("/tmp/jobs")


@app.post("/run")
def run_student(job: dict):

    if "student_id" not in job:
        raise HTTPException(status_code=400, detail="Missing student_id")

    job_id = job.get("job_id", job["student_id"])
    job_dir = BASE_JOB_DIR / job_id

    try:
        job_dir.mkdir(parents=True, exist_ok=False)

        # Save input for debugging
        with open(job_dir / "input.json", "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)

        pdf_path = run_student_pipeline(job, job_dir)

        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name,
        )

    except FileExistsError:
        raise HTTPException(status_code=409, detail="Job already exists")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
