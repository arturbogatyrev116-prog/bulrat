import logging
import re

import httpx

from .base import BaseIngestion
from ..triage import REDDIT_DOMAINS

log = logging.getLogger(__name__)


class RedditIngestion(BaseIngestion):
    async def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return domain in REDDIT_DOMAINS

    async def triage(self, url: str) -> tuple[dict, list[str]]:
        triage_data: dict = {"url": url}
        try:
            # Reddit JSON API
            json_url = url.rstrip("/") + ".json?limit=100"
            headers = {"User-Agent": "bulart-pipeline/1.0"}
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(json_url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            post = data[0]["data"]["children"][0]["data"]
            title = post.get("title", "")
            selftext = post.get("selftext", "")

            comments_data = data[1]["data"]["children"]
            top_comments = []
            for c in comments_data[:10]:
                body = c.get("data", {}).get("body", "")
                if body and body != "[deleted]" and body != "[removed]":
                    top_comments.append(body)

            full_text = f"# {title}\n\n{selftext}\n\n## Top Comments\n\n"
            full_text += "\n\n---\n\n".join(top_comments)

            triage_data["article_title"] = title
            triage_data["article_text"] = full_text.strip()

        except Exception as e:
            log.warning("Reddit triage failed for %s: %s", url, e)
            triage_data["triage_error"] = str(e)

        return triage_data, ["summarization"]
