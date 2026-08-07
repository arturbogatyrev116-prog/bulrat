import logging

from .base import BaseIngestion
from ..triage import triage_article, TWITTER_DOMAINS, REDDIT_DOMAINS

log = logging.getLogger(__name__)


class ArticleIngestion(BaseIngestion):
    async def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return domain not in TWITTER_DOMAINS and domain not in REDDIT_DOMAINS

    async def triage(self, url: str) -> tuple[dict, list[str]]:
        return await triage_article(url)
