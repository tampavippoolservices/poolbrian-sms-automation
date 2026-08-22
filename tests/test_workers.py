from typing import cast

from app import workers
from app.config import AppConfig


def test_process_all_continues_after_independent_step_failure(monkeypatch) -> None:
    called: list[str] = []

    def fail_completed(_config) -> None:
        called.append("completed")
        raise RuntimeError("PoolBrain unavailable")

    monkeypatch.setattr(workers, "recover_stale_work", lambda: {"recovered": 0})
    monkeypatch.setattr(workers, "poll_completed_services", fail_completed)
    monkeypatch.setattr(
        workers,
        "process_inbound_events",
        lambda _config: called.append("events") or {"processed": 1},
    )
    monkeypatch.setattr(
        workers,
        "process_due_messages",
        lambda _config: called.append("messages") or {"accepted": 1},
    )

    result = workers.process_all(cast(AppConfig, object()))

    assert called == ["completed", "events", "messages"]
    assert result["success"] is False
    assert result["errors"] == {"completed_services": "RuntimeError"}
    assert result["messages"] == {"accepted": 1}
