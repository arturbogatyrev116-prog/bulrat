from types import SimpleNamespace

import pytest

import worker.transcribe as transcribe_module


@pytest.mark.asyncio
async def test_transcribe_uses_small_model_when_resources_are_available(monkeypatch: pytest.MonkeyPatch) -> None:
    instances = []

    class FakeModel:
        def __init__(self, name: str, **kwargs: str) -> None:
            self.name = name
            self.kwargs = kwargs
            instances.append(self)

        def transcribe(self, path: str, language: str):
            assert path == "input.wav"
            assert language == "ru"
            return iter([SimpleNamespace(text=" Привет "), SimpleNamespace(text="мир")]), object()

    monkeypatch.setattr(transcribe_module, "WhisperModel", FakeModel)
    monkeypatch.setattr(transcribe_module.psutil, "cpu_percent", lambda interval=None: 20)
    monkeypatch.setattr(transcribe_module.psutil, "virtual_memory", lambda: SimpleNamespace(available=4 * 1024 ** 3))
    transcribe_module._get_model.cache_clear()

    assert await transcribe_module.transcribe("input.wav") == "Привет мир"
    assert instances[0].name == "small"
    assert instances[0].kwargs == {"device": "cpu", "compute_type": "int8"}


def test_select_model_uses_base_under_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcribe_module.psutil, "cpu_percent", lambda interval=None: 90)
    monkeypatch.setattr(transcribe_module.psutil, "virtual_memory", lambda: SimpleNamespace(available=8 * 1024 ** 3))

    assert transcribe_module.select_model_name() == "base"
