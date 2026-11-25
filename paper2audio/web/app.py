from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..config import load_settings
from .jobs import Job, create_job, event_stream, get_job, run_job

app = FastAPI(title="paper2audio")

_settings = load_settings()
_cfg = _settings.app()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class JobRequest(BaseModel):
    arxiv_id: str
    level: str = "normal"
    include_appendix: bool = False


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/jobs", response_model=Job)
async def submit_job(req: JobRequest, background_tasks: BackgroundTasks, request: Request):
    job = create_job(req.arxiv_id, req.level, req.include_appendix)
    base_url = str(request.base_url).rstrip("/")
    background_tasks.add_task(run_job, job, _settings, _cfg, base_url)
    return job


@app.get("/jobs/{job_id}")
async def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(event_stream(job_id), media_type="text/event-stream")


@app.get("/jobs/{job_id}/download")
async def download_job(job_id: str):
    job = get_job(job_id)
    if not job or job.status.value != "done":
        raise HTTPException(status_code=404, detail="Job not ready")

    output_dir = (_cfg.cache.outputs_dir / job_id).resolve()
    zip_path = output_dir / "paper2audio.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in output_dir.iterdir():
            if f.suffix in (".m4b", ".mp3", ".html", ".json") and f.name != "paper2audio.zip":
                zf.write(f, f.name)

    title_slug = (job.paper_title or job_id)[:40].replace(" ", "_").replace("/", "_")
    return FileResponse(zip_path, filename=f"paper2audio_{title_slug}.zip")


@app.get("/audio/{job_id}/{filename}")
async def serve_audio(job_id: str, filename: str):
    path = (_cfg.cache.outputs_dir / job_id / filename).resolve()
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/feed.rss")
async def rss_feed():
    rss_path = (_cfg.cache.outputs_dir / "feed.xml").resolve()
    if not rss_path.exists():
        return HTMLResponse("<rss></rss>", media_type="application/rss+xml")
    return FileResponse(rss_path, media_type="application/rss+xml")


@app.get("/health")
async def health():
    return {"status": "ok"}
