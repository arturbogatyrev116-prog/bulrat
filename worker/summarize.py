"""Ollama summarization and robust parsing of its requested JSON response."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Any

import aiohttp


def _prompt_path(task_type: str, config: dict[str, Any]) -> Path:
    prompt_map = config.get("prompt_map", {})
    filename = prompt_map.get(task_type, "generic.md")
    directory = Path(config.get("prompts_dir", "config/prompts"))
    return directory / filename


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


async def summarize(text: str, task_type: str, config: dict[str, Any]) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("Cannot summarize empty text")
    llm = config.get("llm", {})
    endpoint = str(llm.get("base_url", "http://localhost:11434")).rstrip("/") + "/api/generate"
    prompt = build_prompt(text, task_type, config)
    models = [llm.get("model", "qwen2.5:7b")]
    if llm.get("fallback_model") and llm["fallback_model"] != models[0]:
        models.append(llm["fallback_model"])
    timeout = aiohttp.ClientTimeout(total=30)
    last_error: Exception | None = None
    for model in models:
        body = {"model": model, "prompt": prompt, "stream": False}
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(endpoint, json=body) as response:
                        response.raise_for_status()
                        data = await response.json()
                return _parse_response(str(data.get("response", "")))
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
    raise RuntimeError("Ollama summarization failed after retries") from last_error
