# -*- coding: utf-8 -*-
"""FastAPI + uvicorn 启动入口。

启动: python run.py
验证: GET http://127.0.0.1:5000/api/health

"""
import os
import sys

# 优先加载 fastapi_framework 作为新服务层，避免命中根目录旧 Flask app 包
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fastapi_framework"))

import uvicorn  # noqa: E402
from app import create_app  # noqa: E402  指向 fastapi_framework/app 的 FastAPI 工厂

app = create_app()


if __name__ == "__main__":
    print("=" * 60)
    print("  FastAPI Framework 服务启动中...")
    print("  访问地址: http://localhost:5000/agent")
    print("  健康检查: http://127.0.0.1:5000/api/health")
    print("  按 Ctrl+C 停止服务")
    print("=" * 60)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5000,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )