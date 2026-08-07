from pathlib import Path

import pytest

from worker.config_loader import load_config


def test_load_config_applies_defaults_and_normalizes_paths(tmp_path: Path) -> None:
    config_file = tmp_path / "worker.yaml"
    config_file.write_text(
        "worker_id: test\ncoordinator_url: http://example.test/\napi_key: key\nmedia_staging_path: media\n",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config["coordinator_url"] == "http://example.test"
    assert config["media_staging_path"] == str((tmp_path / "media").resolve())
    assert config["transcription"]["model_low_load"] == "small"
    assert config["llm"]["model"] == "qwen2.5:7b"


def test_load_config_requires_connection_and_media_fields(tmp_path: Path) -> None:
    config_file = tmp_path / "worker.yaml"
    config_file.write_text("worker_id: test\n", encoding="utf-8")

    with pytest.raises(ValueError, match="coordinator_url"):
        load_config(config_file)
