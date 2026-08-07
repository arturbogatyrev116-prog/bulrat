from .base import BaseIngestion
from ..triage import triage_arxiv, ARXIV_DOMAINS


class ArxivIngestion(BaseIngestion):
    async def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return domain in ARXIV_DOMAINS

    async def triage(self, url: str) -> tuple[dict, list[str]]:
        return await triage_arxiv(url)
