import logging
import re

import httpx

from .base import BaseIngestion
from ..triage import TWITTER_DOMAINS

log = logging.getLogger(__name__)


class TwitterIngestion(BaseIngestion):
    async def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return domain in TWITTER_DOMAINS

    async def triage(self, url: str) -> tuple[dict, list[str]]:
        triage_data: dict = {"url": url}
        try:
            # Use nitter as a scraping-friendly frontend
            nitter_url = url.replace("twitter.com", "nitter.net").replace("x.com", "nitter.net")
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(nitter_url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()

            # Very basic tweet text extraction
            match = re.search(r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>', resp.text, re.DOTALL)
            if match:
                from html import unescape
                text = unescape(re.sub(r"<[^>]+>", " ", match.group(1))).strip()
                triage_data["article_text"] = text
                triage_data["article_title"] = f"Tweet: {text[:80]}..."

        except Exception as e:
            log.warning("Twitter triage failed for %s: %s", url, e)
            triage_data["triage_error"] = str(e)
            triage_data["article_text"] = f"URL: {url}\n(Ручное извлечение)"

        return triage_data, ["summarization"]
