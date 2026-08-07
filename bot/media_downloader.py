import hashlib
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

MEDIA_STAGING = Path(os.environ.get("MEDIA_STAGING", "../media_staging"))


def _staging_inbox() -> Path:
    p = MEDIA_STAGING / "inbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def download_voice(bot, file_id: str) -> Path | None:
    """Download Telegram voice/audio message to media_staging/inbox/."""
    try:
        file = await bot.get_file(file_id)
        ext = file.file_path.rsplit(".", 1)[-1] if "." in file.file_path else "ogg"
        fhash = hashlib.md5(file_id.encode()).hexdigest()[:8]
        dest = _staging_inbox() / f"voice_{fhash}.{ext}"
        await bot.download_file(file.file_path, destination=dest)
        log.info("Downloaded voice to %s", dest)
        return dest
    except Exception as e:
        log.error("Voice download failed for %s: %s", file_id, e)
        return None


async def download_document(bot, file_id: str, original_name: str) -> Path | None:
    """Download Telegram document (PDF, audio, video) to media_staging/inbox/."""
    try:
        file = await bot.get_file(file_id)
        fhash = hashlib.md5(file_id.encode()).hexdigest()[:8]
        # Preserve extension from original filename
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
        dest = _staging_inbox() / f"doc_{fhash}.{ext}"
        await bot.download_file(file.file_path, destination=dest)
        log.info("Downloaded document to %s (%s)", dest, original_name)
        return dest
    except Exception as e:
        log.error("Document download failed for %s: %s", file_id, e)
        return None
