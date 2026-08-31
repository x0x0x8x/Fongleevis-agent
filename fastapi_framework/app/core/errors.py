# -*- coding: utf-8 -*-
"""统一 JSON 错误处理：404/405 等返回统一 JSON，不泄露路径/栈/版本信息，附 request_id。"""
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_MESSAGES = {
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    429: "Too Many Requests",
    500: "Internal Server Error",
    422: "Validation Error",
}


def _body(status: int, message: str, request: Request) -> dict:
    return {
        "code": status,
        "message": message,
        "request_id": getattr(request.state, "request_id", ""),
        "timestamp": int(time.time()),
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        status = exc.status_code
        message = _MESSAGES.get(status, "Request Error")
        return JSONResponse(status_code=status, content=_body(status, message, request))

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=_body(422, _MESSAGES[422], request))

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content=_body(500, _MESSAGES[500], request))
