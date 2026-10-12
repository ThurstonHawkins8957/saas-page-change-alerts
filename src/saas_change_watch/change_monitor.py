from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class AccountStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class CheckOutcome(StrEnum):
    BASELINE_CREATED = "baseline_created"
    UNCHANGED = "unchanged"
    CHANGE_ALERTED = "change_alerted"


class MonitorError(Exception):
    pass


class PageAlertGateway(Protocol):
    async def scrape_markdown(self, url: str) -> str:
        pass

    async def send_change_email(
        self,
        *,
        recipient: str,
        subject: str,
        text: str,
        idempotency_key: str,
    ) -> str:
        pass


@dataclass(slots=True)
class Watch:
    watch_id: str
    url: str
    fingerprint: str | None = None


@dataclass(slots=True)
class Tenant:
    tenant_id: str
    name: str
    admin_emails: list[str]
    status: AccountStatus = AccountStatus.PENDING
    watches: dict[str, Watch] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CheckResult:
    outcome: CheckOutcome
    fingerprint: str
    previous_fingerprint: str | None
    message_ids: list[str]


class MonitorStore:
    def __init__(self) -> None:
        self.tenants: dict[str, Tenant] = {}

    def onboard(self, tenant_id: str, name: str, admin_emails: list[str]) -> Tenant:
        if tenant_id in self.tenants:
            raise MonitorError(f"Tenant {tenant_id!r} already exists")
        tenant = Tenant(tenant_id=tenant_id, name=name, admin_emails=admin_emails)
        self.tenants[tenant_id] = tenant
        return tenant

    def require_tenant(self, tenant_id: str) -> Tenant:
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
            raise MonitorError(f"Tenant {tenant_id!r} was not found")
        return tenant

    def set_status(self, tenant_id: str, status: AccountStatus) -> Tenant:
        tenant = self.require_tenant(tenant_id)
        tenant.status = status
        return tenant

    def add_admin(self, tenant_id: str, email: str) -> Tenant:
        tenant = self.require_tenant(tenant_id)
        if email not in tenant.admin_emails:
            tenant.admin_emails.append(email)
        return tenant

    def add_watch(self, tenant_id: str, watch_id: str, url: str) -> Watch:
        tenant = self.require_tenant(tenant_id)
        if watch_id in tenant.watches:
            raise MonitorError(f"Watch {watch_id!r} already exists")
        watch = Watch(watch_id=watch_id, url=url)
        tenant.watches[watch_id] = watch
        return watch


class ChangeMonitor:
    def __init__(self, store: MonitorStore, gateway: PageAlertGateway) -> None:
        self._store = store
        self._gateway = gateway

    async def check_watch(self, tenant_id: str, watch_id: str) -> CheckResult:
        tenant = self._store.require_tenant(tenant_id)
        if tenant.status is not AccountStatus.ACTIVE:
            raise MonitorError("Only active tenant accounts can run checks")
        watch = tenant.watches.get(watch_id)
        if watch is None:
            raise MonitorError(f"Watch {watch_id!r} was not found")

        page = await self._gateway.scrape_markdown(watch.url)
        fingerprint = _fingerprint(page)
        previous = watch.fingerprint
        if previous is None:
            watch.fingerprint = fingerprint
            return CheckResult(CheckOutcome.BASELINE_CREATED, fingerprint, None, [])
        if previous == fingerprint:
            return CheckResult(CheckOutcome.UNCHANGED, fingerprint, previous, [])

        message_ids = []
        for recipient in tenant.admin_emails:
            message_ids.append(
                await self._gateway.send_change_email(
                    recipient=recipient,
                    subject=f"Page changed: {tenant.name} / {watch.watch_id}",
                    text=(
                        f"A monitored page changed.\n\nURL: {watch.url}\n"
                        f"Previous fingerprint: {previous}\nNew fingerprint: {fingerprint}"
                    ),
                    idempotency_key=f"page-change:{tenant_id}:{watch_id}:{fingerprint}:{recipient}",
                )
            )
        watch.fingerprint = fingerprint
        return CheckResult(CheckOutcome.CHANGE_ALERTED, fingerprint, previous, message_ids)


def _fingerprint(page: str) -> str:
    normalized = "\n".join(line.rstrip() for line in page.replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
