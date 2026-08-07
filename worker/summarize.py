"""Ollama summarization via HTTPX with JSON result normalization."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx


def _prompt_path(task_type: str, config: dict[str, Any]) -> Path:
    directory = Path(config.get("prompts_dir", "config/prompts"))
    direct = directory / f"{task_type}.md"
    if direct.is_file():
        return direct
    filename = config.get("prompt_map", {}).get(task_type, "generic.md")
    configured = directory / filename
    if configured.is_file():
        return configured
    raise FileNotFoundError(f"No prompt found for task type {task_type!r} in {directory}")


def build_prompt(text: str, task_type: str, config: dict[str, Any]) -> str:
    template = _prompt_path(task_type, config).read_text(encoding="utf-8")
    return template.replace("{transcript}", text)


def _parse_response(response: str) -> dict[str, Any]:
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Ollama response is not valid JSON")
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Ollama response JSON must be an object")
    return parsed


def _normalize_result(parsed: dict[str, Any]) -> dict[str, Any]:
    summary = parsed.get("summary", {})
    if isinstance(summary, str):
        summary = {"brief": summary, "ideas": [], "actions": []}
    if not isinstance(summary, dict):
        summary = {"brief": "", "ideas": [], "actions": []}
    summary.setdefault("brief", "")
    summary.setdefault("ideas", [])
    summary.setdefault("actions", [])

    result: dict[str, Any] = {
        "title": str(parsed.get("title") or "Untitled"),
        "summary": summary,
        "tags": [str(tag) for tag in parsed.get("tags", []) if isinstance(tag, (str, int, float))],
        "metadata": parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {},
    }
    if isinstance(parsed.get("chapters"), list):
        result["chapters"] = parsed["chapters"]
    return result


_MAX_TRANSCRIPT_CHARS = 12_000


async def summarize(text: str, task_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """Send a prompt to the configured Ollama endpoint and return note fields."""
    if not text.strip():
        raise ValueError("Cannot summarize empty text")
    # Trim transcript to avoid exceeding context window
    trimmed = text[:_MAX_TRANSCRIPT_CHARS] + ("…" if len(text) > _MAX_TRANSCRIPT_CHARS else "")
    llm = config.get("llm", {})
    endpoint = str(llm.get("base_url", "http://localhost:11434")).rstrip("/") + "/api/generate"
    timeout_s = float(llm.get("timeout_seconds", 300))
    prompt = build_prompt(trimmed, task_type, config)
    models = [str(llm.get("model", "qwen2.5:7b"))]
    fallback = llm.get("fallback_model")
    if fallback and fallback != models[0]:
        models.append(str(fallback))

    last_error: Exception | None = None
    # trust_env=False prevents system HTTP proxies from intercepting localhost requests
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s), trust_env=False) as client:
        for model in models:
            body = {"model": model, "prompt": prompt, "stream": False}
            for attempt in range(3):
                try:
                    response = await client.post(endpoint, json=body)
                    response.raise_for_status()
                    return _normalize_result(_parse_response(str(response.json().get("response", ""))))
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
    raise RuntimeError("Ollama summarization failed after retries") from last_error