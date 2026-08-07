"""Executable asynchronous loop for the Bulart desktop worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

try:
    # ``python -m worker.main``: regular package execution.
    from .config_loader import load_config
    from .health_monitor import can_claim_task, get_health
    from .media_handler import get_local_path, process_media
    from .office_handler import process_office_document
    from .pdf_handler import extract_text
    from .summarize import summarize
    from .transcribe import select_model_name
    from .vault_updater import CoordinatorClient
except ImportError:
    # ``python worker/main.py``: make the project root importable first.
    # Importing ``worker.*`` preserves the relative imports used internally.
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from worker.config_loader import load_config
    from worker.health_monitor import can_claim_task, get_health
    from worker.media_handler import get_local_path, process_media
    from worker.office_handler import process_office_document
    from worker.pdf_handler import extract_text
    from worker.summarize import summarize
    from worker.transcribe import select_model_name
    from worker.vault_updater import CoordinatorClient


log = logging.getLogger(__name__)


def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in (logging.StreamHandler(), logging.FileHandler(Path(__file__).with_name("worker.log"), encoding="utf-8")):
        handler.setFormatter(formatter)
        root.addHandler(handler)


async def _wait_for_file(task: dict[str, Any], config: dict[str, Any]) -> None:
    path = get_local_path(task, config["media_staging_path"])
    deadline = asyncio.get_running_loop().time() + float(config.get("syncthing_wait_timeout_seconds", 300))
    while not path.is_file():
        if asyncio.get_running_loop().time() >= deadline:
            raise FileNotFoundError(f"Timed out waiting for media file: {path}")
        await asyncio.sleep(float(config.get("syncthing_wait_poll_seconds", 5)))


def _task_text(task: dict[str, Any]) -> str:
    payload = task.get("payload", {})
    triage = task.get("triage_data", {}) or {}
    return str(payload.get("extracted_text") or triage.get("article_text") or payload.get("text") or "").strip()


def _subtitle_text(task: dict[str, Any]) -> str:
    payload = task.get("payload", {})
    triage = task.get("triage_data", {}) or {}
    text = str(payload.get("subtitles_text") or triage.get("subtitles_text") or "").strip()
    return text if len(text) >= 80 else ""


async def _content_for_task(task: dict[str, Any], config: dict[str, Any]) -> tuple[str, str | None, str]:
    """Return source text, optional transcript, and the extraction method."""
    task_type = task["type"]
    payload = task.get("payload", {})
    triage = task.get("triage_data", {}) or {}
    if task_type in {"voice", "audio", "video"}:
        await _wait_for_file(task, config)
        transcript = await process_media(task, config["media_staging_path"], config["transcription"])
        return transcript, transcript, f"whisper:{select_model_name(config['transcription'])}"
    if task_type == "youtube":
        subtitles = _subtitle_text(task)
        if subtitles:
            return subtitles, subtitles, "subtitles"
        await _wait_for_file(task, config)
        transcript = await process_media(task, config["media_staging_path"], config["transcription"])
        return transcript, transcript, f"whisper:{select_model_name(config['transcription'])}"
    if task_type in {"article", "twitter", "reddit", "text"}:
        text = _task_text(task)
        if not text:
            raise ValueError(f"{task_type} task has no extracted text")
        return text, None, "extracted_text"
    if task_type in {"word", "powerpoint", "excel", "odf", "rtf", "epub"}:
        await _wait_for_file(task, config)
        path = get_local_path(task, config["media_staging_path"])
        extracted = await process_office_document(str(path))
        if not extracted:
            raise ValueError(f"{task_type} task contains an unsupported, encrypted, or unreadable document")
        return extracted, None, "anydoc"
    if task_type in {"arxiv", "pdf"}:
        extracted = str(triage.get("pdf_text") or "").strip()
        if extracted:
            return extracted, None, "triage_pdf_text"
        file_reference = payload.get("pdf_file") or payload.get("file_path")
        if file_reference:
            media_task = {"payload": {"file_path": file_reference}}
            await _wait_for_file(media_task, config)
            path = get_local_path(media_task, config["media_staging_path"])
            extracted = extract_text(path, allow_ocr="pdf_ocr" in config.get("capabilities", []))
        if not extracted and task_type == "arxiv":
            extracted = str(payload.get("abstract") or triage.get("abstract") or "").strip()
        if not extracted:
            raise ValueError(f"{task_type} task has no readable PDF text")
        return extracted, None, "pdf_ocr" if triage.get("needs_ocr") else "pdf_text"
    raise ValueError(f"Unsupported task type: {task_type}")


async def build_result(task: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    source, transcript, method = await _content_for_task(task, config)
    result = await summarize(source, task["type"], config)
    payload, triage = task.get("payload", {}), task.get("triage_data", {}) or {}
    result.setdefault("title", payload.get("title") or triage.get("article_title") or f"{task['type']} {task['task_id']}")
    result.setdefault("summary", {"brief": source[:500], "ideas": [], "actions": []})
    result.setdefault("tags", [])
    result.setdefault("chapters", [])
    result["metadata"] = {**result.get("metadata", {}), "method": method, "word_count": len(source.split())}
    if transcript:
        result["transcript"] = transcript
    return result


async def keep_alive(client: CoordinatorClient, task_id: str, config: dict[str, Any]) -> None:
    interval = float(config.get("lease_extend_interval_seconds", 60))
    while True:
        await asyncio.sleep(interval)
        try:
            await client.extend(task_id, int(config.get("lease_minutes", 10)))
        except Exception:
            log.exception("Could not extend lease for task %s", task_id)


async def process_claimed_task(client: CoordinatorClient, task: dict[str, Any], config: dict[str, Any]) -> None:
    task_id = task["task_id"]
    lease_task = asyncio.create_task(keep_alive(client, task_id, config), name=f"lease-{task_id}")
    try:
        result = await build_result(task, config)
        await client.done(task_id, result)
        log.info("Completed task %s", task_id)
    except asyncio.CancelledError:
        await client.fail(task_id, "Worker received shutdown signal while processing task")
        raise
    except Exception as exc:
        log.exception("Task %s failed", task_id)
        await client.fail(task_id, str(exc))
    finally:
        lease_task.cancel()
        try:
            await lease_task
        except asyncio.CancelledError:
            pass


async def _heartbeat_loop(
    client: CoordinatorClient,
    config: dict[str, Any],
    active_tasks: set[asyncio.Task[None]],
    shutdown: asyncio.Event,
) -> None:
    """Report the current number of running tasks every configured interval."""
    while not shutdown.is_set():
        try:
            await client.heartbeat(get_health(len(active_tasks)), config)
        except Exception:
            log.exception("Heartbeat failed")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=float(config.get("heartbeat_interval_seconds", 25)))
        except asyncio.TimeoutError:
            pass


async def _process_with_slot(
    client: CoordinatorClient,
    task: dict[str, Any],
    config: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> None:
    """Process one already-claimed task and always release its capacity slot."""
    try:
        await process_claimed_task(client, task, config)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Unexpected worker error for task %s", task.get("task_id"))
    finally:
        semaphore.release()


async def run_worker(config: dict[str, Any], shutdown: asyncio.Event | None = None) -> None:
    """Claim and process tasks up to ``max_parallel`` until shutdown."""
    shutdown = shutdown or asyncio.Event()
    client = CoordinatorClient(config)
    max_parallel = max(1, int(config.get("max_parallel", 1)))
    semaphore = asyncio.Semaphore(max_parallel)
    active_tasks: set[asyncio.Task[None]] = set()
    heartbeat = asyncio.create_task(_heartbeat_loop(client, config, active_tasks, shutdown), name="heartbeat")

    try:
        while not shutdown.is_set():
            if len(active_tasks) >= max_parallel:
                # Keep the event loop responsive to SIGINT/SIGTERM while all
                # slots are busy; finished tasks remove themselves via callback.
                await asyncio.sleep(0.2)
                continue
            if not can_claim_task():
                await asyncio.sleep(float(config.get("claim_interval_seconds", 5)))
                continue

            await semaphore.acquire()
            try:
                task = await client.claim()
            except Exception:
                semaphore.release()
                log.exception("Task claim failed")
                await asyncio.sleep(float(config.get("claim_interval_seconds", 5)))
                continue
            if task is None:
                semaphore.release()
                await asyncio.sleep(float(config.get("claim_interval_seconds", 5)))
                continue

            processing = asyncio.create_task(
                _process_with_slot(client, task, config, semaphore),
                name=f"task-{task['task_id']}",
            )
            active_tasks.add(processing)
            processing.add_done_callback(active_tasks.discard)
    finally:
        shutdown.set()
        for task in list(active_tasks):
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulart asynchronous worker")
    parser.add_argument("--config", default=os.environ.get("BULART_WORKER_CONFIG", "config/worker_desktop.yaml"))
    args = parser.parse_args()
    configure_logging()
    config = load_config(args.config)
    shutdown = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, shutdown.set)
        except NotImplementedError:  # Windows event loops do not provide add_signal_handler.
            signal.signal(signum, lambda *_args: shutdown.set())
    try:
        loop.run_until_complete(run_worker(config, shutdown))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
