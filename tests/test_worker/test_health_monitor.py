from types import SimpleNamespace

import pytest

from worker import health_monitor


def test_can_claim_task_when_resources_are_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_monitor.psutil, "cpu_percent", lambda interval=None: 45.0)
    monkeypatch.setattr(health_monitor.psutil, "virtual_memory", lambda: SimpleNamespace(available=4 * 1024 ** 3))

    assert health_monitor.can_claim_task() is True


@pytest.mark.parametrize("cpu,ram_gb", [(81.0, 8.0), (20.0, 1.9)])
def test_cannot_claim_task_when_overloaded(monkeypatch: pytest.MonkeyPatch, cpu: float, ram_gb: float) -> None:
    monkeypatch.setattr(health_monitor.psutil, "cpu_percent", lambda interval=None: cpu)
    monkeypatch.setattr(
        health_monitor.psutil, "virtual_memory", lambda: SimpleNamespace(available=ram_gb * 1024 ** 3)
    )

    assert health_monitor.can_claim_task() is False
