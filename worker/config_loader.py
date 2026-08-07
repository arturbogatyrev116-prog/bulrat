"""Worker configuration loading and validation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULTS: dict[str, Any] = {
    "worker_id": "desktop",
    "max_parallel": 1,
    "max_queue": 5,
    "capabilities": [],
    "heartbeat_interval_seconds": 25,
    "claim_interval_seconds": 5,
    "lease_extend_interval_seconds": 60,
    "lease_minutes": 10,
    "syncthing_wait_poll_seconds": 5,
    "syncthing_wait_timeout_seconds": 300,
    "transcription": {
        "engine": "faster-whisper",
        "device": "cpu",
        "compute_type": "int8",
        "model_low_load": "small",
        "model_high_load": "base",
        "low_load_cpu_max": 50,
        "low_load_ram_min_gb": 3.0,
    },
    "llm": {
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": "qwen2.5:7b",
        "fallback_model": "qwen2.5:3b",
        "timeout_seconds": 30,
    },
    "prompt_map": {
        "youtube": "youtube.md", "article": "article.md", "twitter": "article.md",
        "reddit": "article.md", "arxiv": "scientific.md", "voice": "generic.md",
        "video": "meeting.md", "pdf": "scientific.md", "text": "generic.md",
        "word": "generic.md", "powerpoint": "generic.md", "excel": "generic.md",
        "odf": "generic.md", "rtf": "generic.md", "epub": "generic.md",
    },
}


def _merge(defaults: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(defaults)
    for key, value in supplied.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    """Read YAML config and return normalized worker settings.

    Also auto-loads a sibling *.local.yaml if present — local overrides
    contain real IPs/keys and are gitignored.
    """
    config_path = Path(path).expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    if not isinstance(raw, dict):
        raise ValueError("Worker configuration must be a YAML mapping")

    # Auto-merge *.local.yaml override (gitignored, contains real secrets)
    local_path = config_path.with_suffix("").with_suffix(".local.yaml")
    if local_path.exists():
        with local_path.open("r", encoding="utf-8") as f:
            local_overrides = yaml.safe_load(f) or {}
        if isinstance(local_overrides, dict):
            raw = _merge(raw, local_overrides)

    config = _merge(DEFAULTS, raw)
    for required in ("worker_id", "coordinator_url", "api_key"):
        if not config.get(required):
            raise ValueError(f"Missing required worker configuration field: {required}")

    media_path = config.get("media_staging_path", config.get("media_dir"))
    if not media_path:
        raise ValueError("Missing required worker configuration field: media_staging_path")
    media_path = Path(media_path).expanduser()
    if not media_path.is_absolute():
        media_path = (config_path.parent / media_path).resolve()
    config["media_staging_path"] = str(media_path)
    config["media_dir"] = str(media_path)  # compatibility with the initial worker contract
    prompts_path = Path(config.get("prompts_dir", config_path.parent / "prompts"))
    if not prompts_path.is_absolute():
        project_root = config_path.parent.parent if config_path.parent.name == "config" else config_path.parent
        prompts_path = (project_root / prompts_path).resolve()
    config["prompts_dir"] = str(prompts_path)
    config["coordinator_url"] = str(config["coordinator_url"]).rstrip("/")
    config["capabilities"] = list(config["capabilities"])
    return config
