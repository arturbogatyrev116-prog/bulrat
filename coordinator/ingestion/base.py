from abc import ABC, abstractmethod


class BaseIngestion(ABC):
    @abstractmethod
    async def can_handle(self, url: str) -> bool: ...

    @abstractmethod
    async def triage(self, url: str) -> tuple[dict, list[str]]:
        """
        Returns (triage_data, required_capabilities).
        Runs on VPS during triage phase.
        """
        ...
