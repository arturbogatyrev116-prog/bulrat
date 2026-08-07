"""Office-document conversion through Firecrawl AnyDoc."""

from __future__ import annotations

import asyncio
import logging

import anydoc


log = logging.getLogger(__name__)


async def process_office_document(file_path: str) -> str | None:
    """Convert a supported local office document into Markdown.

    Unsupported and encrypted files are not retryable by this worker, so they
    deliberately yield ``None``. Other converter failures are logged and also
    return no content, allowing the task lifecycle to report a useful failure.
    """
    try:
        return await asyncio.to_thread(anydoc.to_markdown, file_path)
    except (anydoc.UnsupportedError, anydoc.EncryptedError) as exc:
        log.warning("Office document cannot be converted (%s): %s", file_path, exc)
        return None
    except (anydoc.ConvertError, OSError) as exc:
        log.error("Office document conversion failed (%s): %s", file_path, exc)
        return None
    except Exception:
        log.exception("Unexpected office document conversion error: %s", file_path)
        return None
