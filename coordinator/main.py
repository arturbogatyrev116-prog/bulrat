import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .database import init_db
from .queue import (
    claim_task, create_task, extend_lease, get_task, list_tasks,
    mark_done, mark_failed, requeue_expired, retry_task, set_triage_data,
)
from .registry import get_all_workers, register_heartbeat
from .vault_writer import VAULT_PATH, git_pull, scan_vault_inbox, write_failed_note, write_note
from .triage import detect_url_type, triage_pdf, triage_office, OFFICE_TYPES

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

API_KEY = os.environ.get("API_KEY", "change-me-secret")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "config/pipeline.yaml")
MEDIA_STAGING = Path(os.environ.get("MEDIA_STAGING", "media_staging"))

_config: dict = {}


def _load_config():
    global _config
    try:
        with open(CONFIG_PATH) as f:
            _config = yaml.safe_load(f)
    except FileNotFoundError:
        _config = {"lease_duration_minutes": 10, "max_attempts": 3}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _load_config()
    asyncio.create_task(_lease_checker())
    asyncio.create_task(_vault_watcher())
    yield


app = FastAPI(title="Bulart Coordinator", lifespan=lifespan)
templates = Jinja2Templates(directory="coordinator/dashboard/templates")


def _require_api_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_task_id(type_: str) -> str:
    prefix = {
        "youtube": "yt", "article": "ar", "voice": "vo", "video": "vi",
        "pdf": "pd", "twitter": "tw", "reddit": "rd", "arxiv": "ax",
        "word": "wd", "powerpoint": "pp", "excel": "xl",
        "odf": "od", "rtf": "rt", "epub": "ep", "text": "tx",
    }.get(type_, "xx")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand = hashlib.md5(os.urandom(8)).hexdigest()[:6]
    return f"{ts}_{prefix}_{rand}"


# --- Background loops ---

async def _lease_checker():
    max_attempts = _config.get("max_attempts", 3)
    while True:
        await asyncio.sleep(60)
        try:
            requeue_expired(max_attempts=max_attempts)
        except Exception as e:
            log.error("Lease checker error: %s", e)


async def _vault_watcher():
    """Poll Obsidian vault for new task cards."""
    while True:
        await asyncio.sleep(60)
        try:
            git_pull(VAULT_PATH)
            items = scan_vault_inbox(VAULT_PATH)
            for item in items:
                _ingest_vault_task(item)
        except Exception as e:
            log.error("Vault watcher error: %s", e)


def _ingest_vault_task(item: dict):
    td = item["task_data"]
    task_type = td.get("type", "text")
    url = td.get("url", "")
    text = td.get("text", "")
    file_path = td.get("file", "")

    if url:
        payload = {"url": url, "title": td.get("title", "")}
    elif file_path:
        payload = {"file_path": file_path}
    else:
        payload = {"text": text, "title": td.get("title", "")}

    task_id = td.get("task_id") or _make_task_id(task_type)
    try:
        create_task(task_id, task_type, payload, priority=50, source="obsidian")
        log.info("Ingested vault task: %s", task_id)
        asyncio.create_task(_run_triage(task_id, task_type, payload))
    except Exception as e:
        log.error("Failed to ingest vault task %s: %s", task_id, e)


# --- Request/Response models ---

class CreateTaskRequest(BaseModel):
    type: str
    payload: dict
    priority: int = Field(50, ge=0, le=100)
    source: str = "api"


class ClaimRequest(BaseModel):
    worker_id: str
    capabilities: list[str]


class ExtendRequest(BaseModel):
    worker_id: str
    lease_minutes: int = Field(10, ge=1, le=60)


class DoneRequest(BaseModel):
    worker_id: str
    result: dict


class FailRequest(BaseModel):
    worker_id: str
    error: str


class HeartbeatRequest(BaseModel):
    worker_id: str
    cpu_load: float = 0.0
    ram_free_gb: float = 0.0
    disk_free_gb: float = 0.0
    active_tasks: int = 0
    queued_tasks: int = 0
    capabilities: list[str] = []
    on_battery: bool = False
    max_parallel: int = 1
    max_queue: int = 5


# --- Routes ---

@app.post("/tasks", status_code=201, dependencies=[Depends(_require_api_key)])
async def post_task(body: CreateTaskRequest, background_tasks: BackgroundTasks):
    task_id = _make_task_id(body.type)
    result = create_task(task_id, body.type, body.payload, body.priority, body.source)
    background_tasks.add_task(_run_triage, task_id, body.type, body.payload)
    return result


async def _run_triage(task_id: str, task_type: str, payload: dict):
    """Run type-specific triage on VPS, update task with results."""
    try:
        from .ingestion.youtube import YouTubeIngestion
        from .ingestion.article import ArticleIngestion
        from .ingestion.twitter import TwitterIngestion
        from .ingestion.reddit import RedditIngestion
        from .ingestion.arxiv import ArxivIngestion

        url = payload.get("url", "")
        file_path = payload.get("file_path", "")

        if task_type == "youtube":
            triage_data, caps = await YouTubeIngestion().triage(url)
        elif task_type == "twitter":
            triage_data, caps = await TwitterIngestion().triage(url)
        elif task_type == "reddit":
            triage_data, caps = await RedditIngestion().triage(url)
        elif task_type == "arxiv":
            triage_data, caps = await ArxivIngestion().triage(url)
        elif task_type == "article":
            triage_data, caps = await ArticleIngestion().triage(url)
        elif task_type == "pdf":
            triage_data, caps = await triage_pdf(file_path)
        elif task_type in ("voice", "video"):
            triage_data, caps = {}, ["heavy_transcription", "summarization"]
        elif task_type in OFFICE_TYPES:
            triage_data, caps = await triage_office(file_path, task_type)
        elif task_type == "text":
            triage_data, caps = {}, ["summarization"]
        else:
            triage_data, caps = {}, ["summarization"]

        set_triage_data(task_id, triage_data, caps)
        log.info("Triage done for %s: caps=%s", task_id, caps)

    except Exception as e:
        log.error("Triage failed for %s: %s", task_id, e)


@app.get("/tasks", dependencies=[Depends(_require_api_key)])
def get_tasks(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    tasks, total = list_tasks(status, limit, offset)
    return {"tasks": tasks, "total": total}


@app.get("/tasks/{task_id}", dependencies=[Depends(_require_api_key)])
def get_task_detail(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/tasks/claim", dependencies=[Depends(_require_api_key)])
def claim(body: ClaimRequest):
    lease_minutes = _config.get("lease_duration_minutes", 10)
    task = claim_task(body.worker_id, body.capabilities, lease_minutes)
    if task is None:
        return HTMLResponse(status_code=204)

    # Return only the fields worker needs
    payload = json.loads(task["payload"]) if isinstance(task["payload"], str) else task["payload"]
    triage_data = json.loads(task.get("triage_data") or "{}") if isinstance(task.get("triage_data"), str) else (task.get("triage_data") or {})
    return {
        "task_id": task["task_id"],
        "type": task["type"],
        "payload": payload,
        "required_capabilities": json.loads(task.get("required_capabilities") or "[]"),
        "triage_data": triage_data,
        "lease_until": task["lease_until"],
    }


@app.post("/tasks/{task_id}/extend", dependencies=[Depends(_require_api_key)])
def extend(task_id: str, body: ExtendRequest):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("assigned_to") != body.worker_id:
        raise HTTPException(status_code=403, detail="Not your task")

    new_lease = extend_lease(task_id, body.worker_id, body.lease_minutes)
    if not new_lease:
        raise HTTPException(status_code=403, detail="Could not extend lease")
    return {"lease_until": new_lease}


@app.post("/tasks/{task_id}/done", dependencies=[Depends(_require_api_key)])
def done(task_id: str, body: DoneRequest, background_tasks: BackgroundTasks):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("assigned_to") != body.worker_id:
        raise HTTPException(status_code=403, detail="Not your task")

    # Write vault note in background so HTTP response is fast
    background_tasks.add_task(_write_and_finalize, task, body.result)
    note_path = f"1-Notes/{task_id}.md"
    return {"status": "done", "note_path": note_path}


def _write_and_finalize(task: dict, result: dict):
    try:
        task_payload = json.loads(task["payload"]) if isinstance(task["payload"], str) else task["payload"]
        note_path = write_note(task, result, VAULT_PATH)
        mark_done(task["task_id"], task["assigned_to"], result, note_path)
    except Exception as e:
        log.error("vault_writer failed for %s: %s", task["task_id"], e)
        mark_failed(
            task["task_id"],
            task["assigned_to"],
            f"vault_writer error: {e}",
            max_attempts=999,  # don't retry vault write failures automatically
        )


@app.post("/tasks/{task_id}/fail", dependencies=[Depends(_require_api_key)])
def fail(task_id: str, body: FailRequest):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("assigned_to") != body.worker_id:
        raise HTTPException(status_code=403, detail="Not your task")

    max_attempts = _config.get("max_attempts", 3)
    result = mark_failed(task_id, body.worker_id, body.error, max_attempts)

    if result["status"] == "failed":
        try:
            updated_task = get_task(task_id)
            write_failed_note(updated_task, VAULT_PATH)
        except Exception as e:
            log.error("Failed to write failed note for %s: %s", task_id, e)

    return result


@app.post("/tasks/{task_id}/retry", dependencies=[Depends(_require_api_key)])
def retry(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return retry_task(task_id)


@app.post("/workers/heartbeat", dependencies=[Depends(_require_api_key)])
def heartbeat(body: HeartbeatRequest):
    register_heartbeat(
        body.worker_id, body.cpu_load, body.ram_free_gb, body.disk_free_gb,
        body.active_tasks, body.queued_tasks, body.capabilities,
        body.on_battery, body.max_parallel, body.max_queue,
    )
    return {"ok": True}


@app.get("/workers", dependencies=[Depends(_require_api_key)])
def workers():
    return {"workers": get_all_workers()}


@app.get("/dashboard")
def dashboard(request: Request, key: str = Query("")):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid key")
    tasks, total = list_tasks(limit=100)
    worker_list = get_all_workers()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "tasks": tasks,
        "workers": worker_list,
        "total": total,
    })


@app.get("/health")
def health():
    return {"status": "ok", "time": _now_iso()}
