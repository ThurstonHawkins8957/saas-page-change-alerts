import asyncio

import pytest

from saas_change_watch.change_monitor import (
    AccountStatus,
    ChangeMonitor,
    CheckOutcome,
    MonitorError,
    MonitorStore,
)


class FakeGateway:
    def __init__(self, pages: list[str]) -> None:
        self.pages = iter(pages)
        self.emails: list[dict[str, str]] = []

    async def scrape_markdown(self, url: str) -> str:
        return next(self.pages)

    async def send_change_email(self, **message: str) -> str:
        self.emails.append(message)
        return f"message-{len(self.emails)}"


def active_monitor(pages: list[str]) -> tuple[ChangeMonitor, MonitorStore, FakeGateway]:
    store = MonitorStore()
    store.onboard("acme", "Acme Cloud", ["ops@example.com", "admin@example.com"])
    store.set_status("acme", AccountStatus.ACTIVE)
    store.add_watch("acme", "pricing", "https://example.com/pricing")
    gateway = FakeGateway(pages)
    return ChangeMonitor(store, gateway), store, gateway


def test_alerts_only_when_an_existing_baseline_changes() -> None:
    monitor, _store, gateway = active_monitor(["Version one\n", "Version one", "Version two"])

    baseline = asyncio.run(monitor.check_watch("acme", "pricing"))
    unchanged = asyncio.run(monitor.check_watch("acme", "pricing"))
    changed = asyncio.run(monitor.check_watch("acme", "pricing"))

    assert baseline.outcome is CheckOutcome.BASELINE_CREATED
    assert unchanged.outcome is CheckOutcome.UNCHANGED
    assert changed.outcome is CheckOutcome.CHANGE_ALERTED
    assert changed.previous_fingerprint == baseline.fingerprint
    assert changed.fingerprint != baseline.fingerprint
    assert changed.message_ids == ["message-1", "message-2"]
    assert [email["recipient"] for email in gateway.emails] == [
        "ops@example.com",
        "admin@example.com",
    ]
    assert all(email["idempotency_key"].startswith("page-change:acme:pricing:") for email in gateway.emails)


def test_suspended_account_cannot_check_a_watch() -> None:
    monitor, store, gateway = active_monitor(["Version one"])
    store.set_status("acme", AccountStatus.SUSPENDED)

    with pytest.raises(MonitorError, match="Only active"):
        asyncio.run(monitor.check_watch("acme", "pricing"))

    assert gateway.emails == []
