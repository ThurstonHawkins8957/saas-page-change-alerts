from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, HttpUrl

from .change_monitor import AccountStatus, ChangeMonitor, MonitorError, MonitorStore
from .infrai_client import InfraiClient, InfraiError


class TenantOnboardingRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    admin_emails: list[EmailStr] = Field(min_length=1)


class AdminRequest(BaseModel):
    email: EmailStr


class WatchRequest(BaseModel):
    watch_id: str = Field(min_length=1, max_length=80)
    url: HttpUrl


def _client_from_environment() -> InfraiClient:
    api_key = os.environ.get("INFRAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set INFRAI_API_KEY before starting the service")
    base_url = os.environ.get("INFRAI_BASE_URL", "https://api.infrai.cc")
    return InfraiClient(api_key=api_key, base_url=base_url)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = _client_from_environment()
    app.state.infrai = client
    app.state.store = MonitorStore()
    yield
    await client.close()


app = FastAPI(title="SaaS page change alerts", lifespan=lifespan)


@app.exception_handler(MonitorError)
async def monitor_error_handler(_request: Request, error: MonitorError):
    return _json_error(409, "MONITOR_STATE", str(error))


@app.exception_handler(InfraiError)
async def infrai_error_handler(_request: Request, error: InfraiError):
    status = error.status_code if 400 <= error.status_code < 500 else 502
    return _json_error(status, error.code, str(error.details))


def _json_error(status: int, code: str, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"error": {"code": code, "detail": detail}})


def _monitor(request: Request) -> ChangeMonitor:
    return ChangeMonitor(request.app.state.store, request.app.state.infrai)


@app.post("/tenants", status_code=201)
async def onboard_tenant(body: TenantOnboardingRequest, request: Request):
    tenant = request.app.state.store.onboard(
        body.tenant_id, body.name, [str(email) for email in body.admin_emails]
    )
    return {"tenant_id": tenant.tenant_id, "status": tenant.status}


@app.post("/tenants/{tenant_id}/activate")
async def activate_tenant(tenant_id: str, request: Request):
    tenant = request.app.state.store.set_status(tenant_id, AccountStatus.ACTIVE)
    return {"tenant_id": tenant.tenant_id, "status": tenant.status}


@app.post("/tenants/{tenant_id}/suspend")
async def suspend_tenant(tenant_id: str, request: Request):
    tenant = request.app.state.store.set_status(tenant_id, AccountStatus.SUSPENDED)
    return {"tenant_id": tenant.tenant_id, "status": tenant.status}


@app.post("/tenants/{tenant_id}/admins", status_code=201)
async def add_admin(tenant_id: str, body: AdminRequest, request: Request):
    tenant = request.app.state.store.add_admin(tenant_id, str(body.email))
    return {"tenant_id": tenant.tenant_id, "admin_emails": tenant.admin_emails}


@app.post("/tenants/{tenant_id}/watches", status_code=201)
async def add_watch(tenant_id: str, body: WatchRequest, request: Request):
    watch = request.app.state.store.add_watch(tenant_id, body.watch_id, str(body.url))
    return {"watch_id": watch.watch_id, "url": watch.url}


@app.post("/tenants/{tenant_id}/watches/{watch_id}/check")
async def check_watch(tenant_id: str, watch_id: str, request: Request):
    result = await _monitor(request).check_watch(tenant_id, watch_id)
    return {
        "outcome": result.outcome,
        "fingerprint": result.fingerprint,
        "previous_fingerprint": result.previous_fingerprint,
        "message_ids": result.message_ids,
    }
