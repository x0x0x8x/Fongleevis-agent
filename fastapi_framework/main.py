# -*- coding: utf-8 -*-
"""FastAPI 主框架启动入口。

启动: python main.py
验证: GET http://127.0.0.1:8000/api/health

"""
import uvicorn

from app import create_app

app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, proxy_headers=True, forwarded_allow_ips="*")
