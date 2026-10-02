"""统一错误契约与 trace_id 注入。

ARCH.md §4/§6 要求错误统一返回 `code`、`message`、`retryable`、`trace_id`。
本模块把 FastAPI 的 `HTTPException` 以及未捕获异常转换为统一封套，
并为每个请求分配可追踪的 trace_id。
"""

import re
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

TRACE_HEADER = "X-Trace-Id"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# 错误码语义：可重试（限流/暂时不可用）、需补充（缺字段）、需人工、不可恢复。
RETRYABLE_CODES = {
    "IDEMPOTENCY_IN_PROGRESS",
    "RATE_LIMITED",
    "SERVICE_UNAVAILABLE",
}

HANDOFF_CODES = {
    "IDEMPOTENCY_RESULT_MISSING",
    "ORDER_INVALID_TRANSITION",
}

DEFAULT_MESSAGES = {
    "PRODUCT_NOT_FOUND": "产品不存在或已下架。",
    "MIXED_CURRENCY": "所选产品币种不一致，无法合并报价。",
    "QUOTE_NOT_FOUND": "报价不存在。",
    "QUOTE_VERSION_CONFLICT": "报价已更新，请重新获取。",
    "QUOTE_EXPIRED": "报价已过期。",
    "QUOTE_NOT_CONFIRMED": "报价尚未确认。",
    "IDEMPOTENCY_KEY_REUSED": "幂等键已用于其它请求。",
    "IDEMPOTENCY_RESULT_MISSING": "幂等结果缺失，需要人工核查。",
    "IDEMPOTENCY_IN_PROGRESS": "相同请求正在处理中，请稍后重试。",
    "ORDER_NOT_FOUND": "订单不存在。",
    "ORDER_INVALID_TRANSITION": "订单状态迁移不合法。",
    "ADMIN_AUTH_REQUIRED": "需要运营后台凭证。",
    "ADMIN_AUTH_INVALID": "运营后台凭证无效。",
    "VALIDATION_ERROR": "请求参数不合法。",
    "INTERNAL_ERROR": "服务内部错误。",
}


def resolve_trace_id(request: Request) -> str:
    """沿用上游传入的合法 trace_id，否则生成新的。"""
    incoming = request.headers.get(TRACE_HEADER)
    if incoming and _TRACE_ID_PATTERN.match(incoming):
        return incoming
    return uuid4().hex


def error_payload(code: str, trace_id: str, message: str | None = None) -> dict:
    return {
        "data": None,
        "error": {
            "code": code,
            "message": message or DEFAULT_MESSAGES.get(code, "请求失败。"),
            "retryable": code in RETRYABLE_CODES,
            "handoff_required": code in HANDOFF_CODES,
        },
        "trace_id": trace_id,
    }


def _extract_code(detail: object) -> str | None:
    """兼容既有 `detail={"code": ...}` 写法。"""
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str):
            return code
    return None


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def attach_trace_id(request: Request, call_next):
        trace_id = resolve_trace_id(request)
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers[TRACE_HEADER] = trace_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", None) or resolve_trace_id(request)
        code = _extract_code(exc.detail)
        if code is None:
            # 未标注 code 的 HTTPException：按状态码归类，保持封套一致。
            code = {
                401: "ADMIN_AUTH_REQUIRED",
                403: "ADMIN_AUTH_REQUIRED",
                404: "NOT_FOUND",
                405: "METHOD_NOT_ALLOWED",
                409: "CONFLICT",
                422: "VALIDATION_ERROR",
                429: "RATE_LIMITED",
            }.get(exc.status_code, "HTTP_ERROR")
            message = exc.detail if isinstance(exc.detail, str) else None
            payload = error_payload(code, trace_id, message)
        else:
            payload = error_payload(code, trace_id)
        return JSONResponse(
            status_code=exc.status_code, content=payload, headers={TRACE_HEADER: trace_id}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", None) or resolve_trace_id(request)
        payload = error_payload("VALIDATION_ERROR", trace_id)
        payload["error"]["fields"] = [
            {"loc": [str(part) for part in err.get("loc", [])], "type": err.get("type", "")}
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content=payload, headers={TRACE_HEADER: trace_id})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", None) or resolve_trace_id(request)
        return JSONResponse(
            status_code=500,
            content=error_payload("INTERNAL_ERROR", trace_id),
            headers={TRACE_HEADER: trace_id},
        )
