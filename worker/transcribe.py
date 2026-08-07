"""faster-whisper integration with load-aware model selection."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import psutil

try:  # Keeping module import cheap makes non-media workers usable without Whisper installed.
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - exercised in deployments without optional dependency
    WhisperModel = None  # type: ignore[assignment]


def available_ram_gb() -> float:
    return psutil.virtual_memory().available / (1024 ** 3)


def select_model_name(transcription_config: dict[str, Any] | None = None) -> str:
    settings = transcription_config or {}
    if (
        psutil.cpu_percent(interval=None) < float(settings.get("low_load_cpu_max", 50))
        and available_ram_gb() > float(settings.get("low_load_ram_min_gb", 3.0))
    ):
        return str(settings.get("model_low_load", "small"))
    return str(settings.get("model_high_load", "base"))


@lru_cache(maxsize=2)
def _get_model(model_name: str, device: str, compute_type: str) -> Any:
    if WhisperModel is None:
        raise RuntimeError("faster-whisper is not installed")
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def choose_model(transcription_config: dict[str, Any] | None = None) -> Any:
    """Return a cached CPU Whisper model suited to the current load."""
    settings = transcription_config or {}
    return _get_model(
        select_model_name(settings),
        str(settings.get("device", "cpu") if settings.get("device") != "auto" else "cpu"),
        str(settings.get("compute_type", "int8") if settings.get("compute_type") != "auto" else "int8"),
    )


def _transcribe_sync(audio_path: str, settings: dict[str, Any]) -> str:
    model = choose_model(settings)
    segments, _info = model.transcribe(audio_path, language="ru")
    return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()


async def transcribe(audio_path: str, transcription_config: dict[str, Any] | None = None) -> str:
    """Run blocking inference away from the asyncio event loop."""
    return await asyncio.to_thread(_transcribe_sync, audio_path, transcription_config or {})
