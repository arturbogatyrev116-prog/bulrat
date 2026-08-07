from pathlib import Path

import pytest

from worker import media_handler


@pytest.mark.asyncio
async def test_process_video_extracts_normalizes_and_transcribes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "meeting.mp4"
    video.touch()
    extracted = tmp_path / "meeting.wav"
    normalized = tmp_path / "meeting.normalized.wav"
    calls: list[Path] = []

    async def fake_extract(path: Path) -> Path:
        calls.append(path)
        return extracted

    async def fake_normalize(path: Path) -> Path:
        calls.append(path)
        return normalized

    async def fake_transcribe(path: str, config: dict) -> str:
        assert Path(path) == normalized
        assert config == {"model_low_load": "small"}
        return "готовый текст"

    monkeypatch.setattr(media_handler, "extract_audio_ffmpeg", fake_extract)
    monkeypatch.setattr(media_handler, "normalize_audio", fake_normalize)
    monkeypatch.setattr(media_handler, "transcribe", fake_transcribe)

    text = await media_handler.process_media(
        {"payload": {"file_path": "meeting.mp4"}}, tmp_path, {"model_low_load": "small"}
    )

    assert text == "готовый текст"
    assert calls == [video, extracted]


def test_get_local_path_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside media_staging_path"):
        media_handler.get_local_path({"payload": {"file_path": "../outside.mp3"}}, tmp_path)
