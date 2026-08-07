"""Safe, asynchronous media preparation for Whisper."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .transcribe import transcribe


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def is_video(file_path: str | Path) -> bool:
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def get_local_path(task: dict[str, Any], media_dir: str | Path) -> Path:
    """Resolve a task media reference and reject paths outside media staging."""
    payload = task.get("payload", task)
    triage = task.get("triage_data", {}) or {}
    reference = (
        payload.get("file_path") or payload.get("audio_file") or payload.get("pdf_file")
        or triage.get("file_path") or triage.get("audio_file")
    )
    if not reference:
        raise ValueError("Task payload does not contain a media file path")
    root = Path(media_dir).resolve()
    candidate = Path(str(reference))
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Media path must be inside media_staging_path") from exc
    return path


async def _run_ffmpeg(*arguments: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", *arguments,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace').strip()}")


async def extract_audio_ffmpeg(video_path: str | Path) -> Path:
    video = Path(video_path)
    output = video.with_suffix(".wav")
    await _run_ffmpeg("-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output))
    return output


async def normalize_audio(audio_path: str | Path) -> Path:
    source = Path(audio_path)
    output = source.with_name(f"{source.stem}.normalized.wav")
    await _run_ffmpeg("-i", str(source), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output))
    return output


async def process_media(task: dict[str, Any], media_dir: str | Path, transcription_config: dict[str, Any] | None = None) -> str:
    path = get_local_path(task, media_dir)
    if not path.is_file():
        raise FileNotFoundError(f"Media file has not arrived in staging: {path}")
    audio_path = await extract_audio_ffmpeg(path) if is_video(path) else path
    normalized = await normalize_audio(audio_path)
    return await transcribe(str(normalized), transcription_config)
