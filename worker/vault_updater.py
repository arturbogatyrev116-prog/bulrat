"""Coordinator HTTP client with bounded retry/backoff behaviour."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp


log = logging.getLogger(__name__)


class CoordinatorClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = str(config["coordinator_url"]).rstrip("/")
        self.worker_id = str(config["worker_id"])
        self.capabilities = list(config.get("capabilities", []))
        self._headers = {"X-API-Key": str(config["api_key"])}
        self._timeout = aiohttp.ClientTimeout(total=30)

    async def _post(self, path: str, payload: dict[str, Any], *, permit_204: bool = False) -> dict[str, Any] | None:
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(headers=self._headers, timeout=self._timeout) as session:
                    async with session.post(f"{self.base_url}{path}", json=payload) as response:
                        if permit_204 and response.status == 204:
                            return None
                        response.raise_for_status()
                        return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"Coordinator request {path} failed after 3 attempts") from exc
                delay = 2 ** attempt
                log.warning("Coordinator request %s failed (%s); retrying in %ss", path, exc, delay)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def claim(self) -> dict[str, Any] | None:
        return await self._post("/tasks/claim", {"worker_id": self.worker_id, "capabilities": self.capabilities}, permit_204=True)

    async def extend(self, task_id: str, lease_minutes: int = 10) -> dict[str, Any] | None:
        return await self._post(f"/tasks/{task_id}/extend", {"worker_id": self.worker_id, "lease_minutes": lease_minutes})

    async def done(self, task_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
        return await self._post(f"/tasks/{task_id}/done", {"worker_id": self.worker_id, "result": result})

    async def fail(self, task_id: str, error: str) -> dict[str, Any] | None:
        return await self._post(f"/tasks/{task_id}/fail", {"worker_id": self.worker_id, "error": error})

    async def heartbeat(self, health: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
        payload = {**health, "worker_id": self.worker_id, "capabilities": self.capabilities,
                   "max_parallel": config.get("max_parallel", 1), "max_queue": config.get("max_queue", 5)}
        return await self._post("/workers/heartbeat", payload)
