# -*- coding: utf-8 -*-
"""FastAPI 主框架应用工厂：安全默认配置 + 全局安全中间件 + 统一错误处理 + 静态白名单托管 + 子站点注册机制。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from fastapi.responses import FileResponse

from .core.config import SecureDefaultConfig
from .core.errors import register_exception_handlers
from .core.middleware import SecurityMiddleware
from .core.security import SecurityHeadersMiddleware
from .core.static import create_static_router
from .registry.registry import SubsiteRegistry
from .registry.admin_api import router as admin_router
from .core.resources import ResourceManager, create_resource_router


def _init_registry(app, cfg):
    reg = SubsiteRegistry(persist_path=cfg.REGISTRY_PATH, hot_mount=cfg.REGISTRY_HOT_MOUNT)
    if not reg.load():
        return reg
    reg.mount_all(app)
    app.include_router(admin_router)
    app.state.registry = reg
    return reg


def create_app(config_class=None, auth_hook=None):
    cfg = config_class or SecureDefaultConfig
    cfg.load_env_file()
    cfg.validate()

    app = FastAPI(
        title=cfg.APP_NAME,
        version=cfg.VERSION,
        docs_url=None if not cfg.DEBUG else "/docs",
        redoc_url=None if not cfg.DEBUG else "/redoc",
        openapi_url=None if not cfg.DEBUG else "/openapi.json",
    )
    app.state.config = cfg
    app.state.auth_hook = auth_hook

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.CORS_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=cfg.CORS_ALLOW_METHODS,
        allow_headers=cfg.CORS_ALLOW_HEADERS,
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SecurityMiddleware)

    register_exception_handlers(app)

    app.include_router(create_static_router())
    # T2 修复：新增 /chat 路由直接托管自包含的聊天气泡页面 chatAI.html
    _chat_html = Path(__file__).resolve().parent / "sweetmido" / "static" / "web" / "chatAI.html"

    @app.get("/chat", include_in_schema=False)
    async def chat_page():
        return FileResponse(_chat_html)

    _init_registry(app, cfg)
    _init_resources(app, cfg)

    return app


def _init_resources(app, cfg):
    mgr = ResourceManager(cfg.SECURE_ROOT)
    mgr.init_dirs()
    mgr.load()
    app.include_router(create_resource_router(mgr))
    app.state.resources = mgr
    return mgr