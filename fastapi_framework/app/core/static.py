# -*- coding: utf-8 -*-
"""静态资源白名单托管：仅白名单前缀目录可访问，敏感路径硬排除，realpath+commonpath 防路径穿越。"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse


def _safe_resolve(root: Path, relative: str) -> Path:
    """将 URL 相对路径解析到磁盘路径，并做 realpath/commonpath 双重校验防穿越。"""
    relative = relative.lstrip("/")
    candidate = (root / relative).resolve()
    root_real = root.resolve()
    # commonpath 校验：candidate 必须位于 root 之内
    try:
        os.path.commonpath([str(candidate), str(root_real)])
    except ValueError:
        raise HTTPException(status_code=404, detail="Not Found")
    if not (str(candidate) == str(root_real) or str(candidate).startswith(str(root_real) + os.sep)):
        raise HTTPException(status_code=404, detail="Not Found")
    return candidate


def create_static_router(allow_prefixes=None, static_root=None, sensitive_suffixes=None) -> APIRouter:
    """创建静态资源白名单路由。
    allow_prefixes: 允许的 URL 前缀列表（如 ["/static/public"]），未在前缀内一律 404。
    static_root: 磁盘静态根目录。
    sensitive_suffixes: 硬排除的文件后缀。
    """
    allow = allow_prefixes or ["/static/public"]
    root = Path(static_root) if static_root else (Path(__file__).resolve().parent.parent / "static")
    excludes = sensitive_suffixes or [
        ".pem", ".p12", ".key", ".log", ".sqlite3", ".db", ".env", ".py", ".pyc", ".json", ".yml", ".yaml", ".ini", ".cfg", ".bat", ".sh"
    ]
    router = APIRouter()

    @router.get("/static/{path:path}")
    async def static_file(path: str, request: Request):
        url_path = "/static/" + path
        # 1. 白名单前缀校验
        matched = False
        for prefix in allow:
            if url_path == prefix or url_path.startswith(prefix.rstrip("/") + "/"):
                matched = True
                break
        if not matched:
            raise HTTPException(status_code=404, detail="Not Found")
        # 2. 敏感后缀硬排除
        lowered = path.lower()
        for suffix in excludes:
            if lowered.endswith(suffix):
                raise HTTPException(status_code=404, detail="Not Found")
        # 3. 敏感路径片段排除
        for part in ("..", ".well-known", "wx_v3", "log", "debug_logs", "executor_logs", "tmp", ".git", ".env"):
            if part.lower() in lowered.split("/"):
                raise HTTPException(status_code=404, detail="Not Found")
        # 4. 路径穿越防护
        file_path = _safe_resolve(root, path)
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(file_path)

    return router
