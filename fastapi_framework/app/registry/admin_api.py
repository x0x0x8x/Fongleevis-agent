# -*- coding: utf-8 -*-
"""子站点管理 API：/api/_internal/subsites。注册/注销/启停/列表，全部需管理员鉴权。"""
from fastapi import APIRouter, Request, Header
from fastapi.exceptions import HTTPException

from ..registry.registry import (
    SubsiteEntry,
    SubsiteRegistry,
    RegistryConflictError,
    RegistryValidationError,
)

router = APIRouter(prefix="/api/_internal/subsites", tags=["internal-subsites"])


def _check_admin(request, x_admin_token, authorization):
    cfg = request.app.state.config
    expected = getattr(cfg, "ADMIN_TOKEN", "") or ""
    if not expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    token = x_admin_token or ""
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_registry(request):
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        raise HTTPException(status_code=500, detail="registry not initialized")
    return reg


@router.get("")
async def list_subsites(request: Request, x_admin_token: str = Header(None), authorization: str = Header(None)):
    _check_admin(request, x_admin_token, authorization)
    return {"code": 0, "data": _get_registry(request).list_all()}


@router.post("")
async def register_subsite(payload: dict, request: Request, x_admin_token: str = Header(None), authorization: str = Header(None)):
    _check_admin(request, x_admin_token, authorization)
    reg = _get_registry(request)
    try:
        entry = reg.register(SubsiteEntry.from_dict(payload))
    except RegistryConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RegistryValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": entry.to_dict()}


@router.delete("/{site_id}")
async def unregister_subsite(site_id: str, request: Request, x_admin_token: str = Header(None), authorization: str = Header(None)):
    _check_admin(request, x_admin_token, authorization)
    reg = _get_registry(request)
    try:
        reg.unregister(site_id)
    except RegistryValidationError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"code": 0, "data": {"site_id": site_id, "status": "removed"}}


@router.patch("/{site_id}")
async def set_subsite_status(site_id: str, payload: dict, request: Request, x_admin_token: str = Header(None), authorization: str = Header(None)):
    _check_admin(request, x_admin_token, authorization)
    reg = _get_registry(request)
    status = (payload or {}).get("status")
    try:
        if status == "disabled":
            reg.disable(site_id)
        elif status == "registered":
            reg.enable(site_id)
        else:
            raise RegistryValidationError("status must be disabled or registered")
    except RegistryValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": reg.get(site_id).to_dict()}