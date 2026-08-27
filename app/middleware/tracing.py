"""Request tracing middleware — inject request_id into contextvars."""

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class TracingMiddleware(BaseHTTPMiddleware):
    """为每个请求生成唯一的 request_id（或透传客户端发来的 X-Request-ID），注入到 contextvars 供日志 Filter 使用，并在响应头中返回 X-Request-ID。
    """

    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request_id_var.set(req_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response