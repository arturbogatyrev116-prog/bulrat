import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from .media_downloader import download_document, download_voice

log = logging.getLogger(__name__)
router = Router()

COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "http://localhost:8080")
API_KEY = os.environ.get("API_KEY", "change-me-secret")
ALLOWED_USERS = {
    uid for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",") if uid
}

URL_RE = re.compile(r"https?://[^\s<>\"]+")

AUDIO_EXTENSIONS = {".ogg", ".mp3", ".m4a", ".wav", ".flac", ".opus", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
PDF_EXTENSIONS = {".pdf"}
OFFICE_EXTENSIONS: dict[str, str] = {
    ".docx": "word",  ".doc": "word",
    ".pptx": "powerpoint", ".ppt": "powerpoint",
    ".xlsx": "excel", ".xls": "excel",
    ".odt": "odf", ".ods": "odf", ".odp": "odf",
    ".rtf": "rtf",
    ".epub": "epub",
}


def _check_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True  # unrestricted if not configured
    return str(user_id) in ALLOWED_USERS


async def _post_task(task_type: str, payload: dict, priority: int = 50) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{COORDINATOR_URL}/tasks",
                json={"type": task_type, "payload": payload, "priority": priority, "source": "telegram"},
                headers={"X-API-Key": API_KEY},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        log.error("Failed to post task: %s", e)
        return None


def _detect_url_type(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return "article"

    if "youtube.com" in domain or "youtu.be" in domain:
        return "youtube"
    if "twitter.com" in domain or "x.com" in domain:
        return "twitter"
    if "reddit.com" in domain:
        return "reddit"
    if "arxiv.org" in domain:
        return "arxiv"
    return "article"


def _media_type_from_ext(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return "voice"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in OFFICE_EXTENSIONS:
        return OFFICE_EXTENSIONS[ext]
    return None


@router.message(Command("start", "help"))
async def cmd_start(message: Message):
    if not _check_allowed(message.from_user.id):
        return

    await message.answer(
        "Bulart — система обработки знаний.\n\n"
        "Отправь мне:\n"
        "• Ссылку (YouTube, статья, Twitter, Reddit, ArXiv)\n"
        "• Голосовое сообщение\n"
        "• Аудио / видео файл\n"
        "• PDF документ\n"
        "• Текст (просто напиши)\n\n"
        "/status — статус очереди"
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    if not _check_allowed(message.from_user.id):
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            tasks_resp = await client.get(
                f"{COORDINATOR_URL}/tasks",
                headers={"X-API-Key": API_KEY},
            )
            workers_resp = await client.get(
                f"{COORDINATOR_URL}/workers",
                headers={"X-API-Key": API_KEY},
            )

        tasks_data = tasks_resp.json()
        workers_data = workers_resp.json()

        from collections import Counter
        status_counts = Counter(t["status"] for t in tasks_data.get("tasks", []))

        lines = ["**Queue:**"]
        for status, count in sorted(status_counts.items()):
            lines.append(f"  {status}: {count}")

        lines.append("\n**Workers:**")
        for w in workers_data.get("workers", []):
            status = "online" if w["online"] else "offline"
            lines.append(f"  {w['worker_id']}: {status}, cpu={w['cpu_load']:.0f}%, tasks={w['active_tasks']}")

        await message.answer("\n".join(lines))
    except Exception as e:
        await message.answer(f"Error: {e}")


@router.message()
async def handle_any(message: Message, bot: Bot):
    if not _check_allowed(message.from_user.id):
        return

    # Voice message
    if message.voice:
        await _handle_voice(message, bot)
        return

    # Audio file
    if message.audio:
        await _handle_audio(message, bot)
        return

    # Document (PDF, video, audio file)
    if message.document:
        await _handle_document(message, bot)
        return

    # Video
    if message.video:
        await _handle_video(message, bot)
        return

    # Text: look for URLs, otherwise treat as text note
    if message.text:
        urls = URL_RE.findall(message.text)
        if urls:
            await _handle_url(message, urls[0])
        else:
            await _handle_text(message)
        return

    # Caption with URL
    if message.caption:
        urls = URL_RE.findall(message.caption)
        if urls:
            await _handle_url(message, urls[0])


async def _handle_voice(message: Message, bot: Bot):
    await message.react([{"type": "emoji", "emoji": "⏳"}]) if hasattr(message, "react") else None
    path = await download_voice(bot, message.voice.file_id)
    if not path:
        await message.reply("Не удалось скачать голосовое.")
        return

    result = await _post_task("voice", {
        "file_path": str(path.relative_to(path.parents[2])),
        "duration_seconds": message.voice.duration,
    }, priority=70)

    if result:
        await message.reply(f"Принято голосовое `{result['task_id']}`")
    else:
        await message.reply("Ошибка при создании задачи.")


async def _handle_audio(message: Message, bot: Bot):
    fname = message.audio.file_name or "audio.mp3"
    path = await download_document(bot, message.audio.file_id, fname)
    if not path:
        await message.reply("Не удалось скачать аудио.")
        return

    result = await _post_task("voice", {
        "file_path": str(path.relative_to(path.parents[2])),
        "original_filename": fname,
        "duration_seconds": message.audio.duration,
    }, priority=60)

    if result:
        await message.reply(f"Принято аудио `{result['task_id']}`")
    else:
        await message.reply("Ошибка при создании задачи.")


async def _handle_video(message: Message, bot: Bot):
    path = await download_document(bot, message.video.file_id, "video.mp4")
    if not path:
        await message.reply("Не удалось скачать видео.")
        return

    result = await _post_task("video", {
        "file_path": str(path.relative_to(path.parents[2])),
        "duration_seconds": message.video.duration,
    }, priority=60)

    if result:
        await message.reply(f"Принято видео `{result['task_id']}`")
    else:
        await message.reply("Ошибка при создании задачи.")


async def _handle_document(message: Message, bot: Bot):
    fname = message.document.file_name or "file.bin"
    media_type = _media_type_from_ext(fname)
    if not media_type:
        ext = Path(fname).suffix.lower()
        await message.reply(
            f"Тип файла `{ext}` не поддерживается.\n"
            "Поддерживаются: mp3/ogg/wav, mp4/mkv, pdf, "
            "docx/doc, pptx/ppt, xlsx/xls, odt/ods/odp, rtf, epub"
        )
        return

    path = await download_document(bot, message.document.file_id, fname)
    if not path:
        await message.reply("Не удалось скачать файл.")
        return

    is_office = Path(fname).suffix.lower() in OFFICE_EXTENSIONS
    priority = 55 if is_office else 60

    result = await _post_task(media_type, {
        "file_path": str(path.relative_to(path.parents[2])),
        "original_filename": fname,
    }, priority=priority)

    if result:
        await message.reply(f"Принят файл ({media_type}) `{result['task_id']}`")
    else:
        await message.reply("Ошибка при создании задачи.")


async def _handle_url(message: Message, url: str):
    task_type = _detect_url_type(url)
    caption = message.text or message.caption or ""
    title = caption.replace(url, "").strip()[:200] or ""

    result = await _post_task(task_type, {"url": url, "title": title}, priority=80)

    if result:
        await message.reply(f"Принято ({task_type}) `{result['task_id']}`")
    else:
        await message.reply("Ошибка при создании задачи.")


async def _handle_text(message: Message):
    text = message.text.strip()
    if len(text) < 20:
        await message.reply("Слишком короткий текст.")
        return

    result = await _post_task("text", {"text": text}, priority=40)

    if result:
        await message.reply(f"Принят текст `{result['task_id']}`")
    else:
        await message.reply("Ошибка при создании задачи.")
