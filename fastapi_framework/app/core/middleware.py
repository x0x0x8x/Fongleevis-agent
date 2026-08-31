# -*- coding: utf-8 -*-
"""全局安全中间件：request_id 注入 / Host 白名单校验 / CORS Origin 白名单校验 / 敏感路径拦截 / 请求限长 / 统一鉴权钩子。"""
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def _json(status: int, message: str, request, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": status,
            "message": message,
            "request_id": getattr(request.state, "request_id", request_id),
            "timestamp": int(time.time()),
        },
    )


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # 1. request_id 注入
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        cfg = request.app.state.config

        # ========== 🔥 统一放行：静态资源 ==========
        path = request.url.path

        # 放行 agent 静态资源
        if path.startswith("/agent/assets/") or path.startswith("/agent/static/"):
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response

        # 放行主应用静态资源
        if path.startswith("/static/") or path.startswith("/static/public/"):
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response

        # 放行常见静态文件扩展名（兜底）
        static_extensions = (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".webp", ".json")
        if any(path.endswith(ext) for ext in static_extensions):
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response

        # 放行根路径静态文件（如 favicon.ico）
        if path in ("/favicon.ico", "/robots.txt"):
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response

        # ========== 后续安全检查（仅针对 API） ==========

        # 2. Host 白名单校验
        host = (request.headers.get("host") or "").lower()
        host_name = host.split(":")[0] if ":" in host else host
        if host_name not in cfg.ALLOWED_HOSTS:
            return _json(400, "Bad Request", request, request_id)

        # 3. CORS Origin 白名单校验（非浏览器请求可不带 Origin，放行）
        origin = request.headers.get("origin")
        if origin and origin not in cfg.CORS_ALLOWED_ORIGINS:
            return _json(403, "Forbidden", request, request_id)

        # 4. 敏感路径拦截（未注册 URL 也统一 404，不泄露存在性）
        if self._is_sensitive_path(path, cfg):
            return _json(404, "Not Found", request, request_id)

        # 5. 请求限长
        if request.method in ("POST", "PUT", "PATCH"):
            cl = request.headers.get("content-length")
            if cl:
                try:
                    if int(cl) > cfg.MAX_CONTENT_LENGTH:
                        return _json(413, "Payload Too Large", request, request_id)
                except ValueError:
                    pass

        # 6. 统一鉴权钩子（auth_hook 由 create_app 注入，返回 None 放行，返回 (status,msg) 拒绝）
        auth_hook = getattr(request.app.state, "auth_hook", None)
        if auth_hook is not None:
            result = auth_hook(request)
            if result is not None:
                status, message = result
                return _json(status, message, request, request_id)

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @staticmethod
    def _is_sensitive_path(path: str, cfg) -> bool:
        lowered = path.lower()
        for prefix in cfg.SENSITIVE_PATH_PREFIXES:
            if lowered == prefix or lowered.startswith(prefix + "/"):
                return True
        for suffix in cfg.SENSITIVE_FILE_SUFFIXES:
            if lowered.endswith(suffix):
                return True
        return False
