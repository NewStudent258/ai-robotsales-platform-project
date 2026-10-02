"""P1 契约测试：统一错误封套、trace_id、后台鉴权与令牌回显策略。"""

from fastapi import HTTPException

from app.core.errors import TRACE_HEADER, error_payload
from app.main import app

ADMIN = {"X-Admin-Token": "test-admin-token"}


async def _seed_product(client) -> dict:
    response = await client.post(
        "/api/v1/products",
        headers=ADMIN,
        json={
            "sku": "CONTRACT-001",
            "slug": "contract-robot",
            "name": "契约测试机器人",
            "description": "用于契约测试",
            "base_price": "100.00",
        },
    )
    assert response.status_code == 201
    return response.json()


# --- 错误封套 ---------------------------------------------------------------


async def test_error_envelope_shape(client):
    """业务错误必须返回 {data, error{code,message,retryable}, trace_id}。"""
    response = await client.get("/api/v1/quotes/999999", headers={"X-Quote-Token": "x" * 64})

    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "QUOTE_NOT_FOUND"
    assert body["error"]["message"]
    assert body["error"]["retryable"] is False
    assert body["trace_id"]


async def test_validation_error_uses_envelope(client):
    """参数校验失败同样走统一封套，并给出字段定位。"""
    response = await client.post(
        "/api/v1/quotes",
        json={"customer_name": "a", "customer_email": "not-an-email", "items": []},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["fields"], "应返回字段级定位"
    assert body["trace_id"]


async def test_documented_error_codes_have_messages():
    """常见错误码都应有面向用户的中文文案。"""
    for code in (
        "QUOTE_EXPIRED",
        "QUOTE_VERSION_CONFLICT",
        "IDEMPOTENCY_KEY_REUSED",
        "ORDER_INVALID_TRANSITION",
        "ADMIN_AUTH_REQUIRED",
    ):
        payload = error_payload(code, "t" * 16)
        assert payload["error"]["message"] != "请求失败。", f"{code} 缺少专用文案"


# --- trace_id ---------------------------------------------------------------


async def test_trace_id_returned_and_echoed(client):
    """响应头回传 trace_id，且合法上游 trace_id 被沿用。"""
    first = await client.get("/health")
    assert first.headers.get(TRACE_HEADER)

    provided = "abc12345deadbeef"
    echoed = await client.get("/health", headers={TRACE_HEADER: provided})
    assert echoed.headers[TRACE_HEADER] == provided


async def test_trace_id_rejects_injected_value(client):
    """非法 trace_id 不应被原样透传（防日志注入）。"""
    response = await client.get("/health", headers={TRACE_HEADER: "bad value!!"})
    assert response.headers[TRACE_HEADER] != "bad value!!"


async def test_trace_id_present_on_error(client):
    """错误响应也必须带 trace_id，便于按trace排查。"""
    response = await client.get("/api/v1/quotes/123456", headers={"X-Quote-Token": "y" * 64})
    assert response.status_code == 404
    assert response.headers.get(TRACE_HEADER)
    assert response.json()["trace_id"] == response.headers[TRACE_HEADER]


# --- 后台鉴权 ---------------------------------------------------------------


async def test_product_write_requires_admin_token(client):
    """缺少后台令牌时写接口拒绝。"""
    response = await client.post(
        "/api/v1/products",
        json={
            "sku": "NOAUTH-1",
            "slug": "noauth",
            "name": "n",
            "description": "d",
            "base_price": "1.00",
        },
    )
    assert response.status_code in (401, 403)
    assert response.json()["error"]["code"] in {"ADMIN_AUTH_REQUIRED", "ADMIN_AUTH_INVALID"}


async def test_product_write_rejects_wrong_admin_token(client):
    """错误令牌被拒绝。"""
    response = await client.post(
        "/api/v1/products",
        headers={"X-Admin-Token": "wrong-token"},
        json={
            "sku": "NOAUTH-2",
            "slug": "noauth2",
            "name": "n",
            "description": "d",
            "base_price": "1.00",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ADMIN_AUTH_INVALID"


async def test_product_write_succeeds_with_admin_token(client):
    """正确令牌可写入。"""
    product = await _seed_product(client)
    assert product["sku"] == "CONTRACT-001"


# --- 访问令牌回显策略 -------------------------------------------------------


async def test_quote_token_returned_once_and_not_on_read(client):
    """令牌仅在创建时返回，后续读取与确认不回显。"""
    product = await _seed_product(client)
    created = await client.post(
        "/api/v1/quotes",
        json={
            "customer_name": "测试用户",
            "customer_email": "contract@example.com",
            "items": [{"product_id": product["id"], "quantity": 1}],
        },
    )
    assert created.status_code == 201
    token = created.json()["access_token"]
    assert token, "创建响应必须返回访问令牌"
    quote_id = created.json()["id"]
    headers = {"X-Quote-Token": token}

    read = await client.get(f"/api/v1/quotes/{quote_id}", headers=headers)
    assert read.status_code == 200
    assert read.json()["access_token"] is None, "读取不应回显访问令牌"

    confirmed = await client.post(
        f"/api/v1/quotes/{quote_id}/confirm", params={"version": 1}, headers=headers
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["access_token"] is None, "确认不应回显访问令牌"


def test_all_routes_have_error_handlers_registered():
    """确保异常处理器已挂载，避免封套在某些路径上意外退化。"""
    from starlette.exceptions import HTTPException as StarletteHTTPException

    assert StarletteHTTPException in app.exception_handlers
    assert (
        HTTPException in app.exception_handlers or StarletteHTTPException in app.exception_handlers
    )
