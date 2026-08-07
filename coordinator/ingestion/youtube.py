import asyncio
import logging
import os
from pathlib import Path

from .base import BaseIngestion
from ..triage import triage_youtube, YOUTUBE_DOMAINS

log = logging.getLogger(__name__)

MEDIA_STAGING = Path(os.environ.get("MEDIA_STAGING", "media_staging"))


class YouTubeIngestion(BaseIngestion):
    async def can_handle(self, url: str) -> bool:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return domain in YOUTUBE_DOMAINS or "youtube.com" in domain

    async def triage(self, url: str) -> tuple[dict, list[str]]:
        triage_data, required = await triage_youtube(url)

        if not triage_data.get("subtitles_available"):
            # Schedule audio download on VPS so Syncthing can deliver it to workers
            try:
                audio_path = await self._download_audio(url, triage_data.get("title", ""))
                if audio_path:
                    triage_data["file_path"] = str(audio_path.relative_to(MEDIA_STAGING)).replace("\\", "/")
            except Exception as e:
                log.error("Audio download failed for %s: %s", url, e)

        return triage_data, required

    async def _download_audio(self, url: str, title: str) -> Path | None:
        try:
            import yt_dlp
        except ImportError:
            log.warning("yt_dlp not available")
            return None

        inbox = MEDIA_STAGING / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        # Use task-safe filename
        import hashlib
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        output_template = str(inbox / f"yt_{url_hash}.%(ext)s")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }],
            "socket_timeout": 30,
        }

        loop = asyncio.get_event_loop()

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            mp3_path = inbox / f"yt_{url_hash}.mp3"
            return mp3_path if mp3_path.exists() else None

        return await loop.run_in_executor(None, _download)
