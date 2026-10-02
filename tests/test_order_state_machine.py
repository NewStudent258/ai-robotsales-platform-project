"""G-01 / G-03：订单状态机 API 与报价乐观锁。

G-01：`POST /orders/{id}/transition` 之前未接线，`PROCESSING`/`COMPLETED`/
`FAILED` 分支无 API 级覆盖。本文件按 ARCH.md §3 的迁移矩阵逐条验证。
G-03：`confirm_quote` 的 `version` 乐观锁此前无用例，本文件覆盖不匹配、
越界与状态前置条件三条路径。
"""

import pytest
from sqlalchemy import select

from app.models.commerce import OrderEvent
from app.services.commerce_service import ORDER_TRANSITIONS, CommerceService

ADMIN = {"X-Admin-Token": "test-admin-token"}


async def _seed_product(client) -> dict:
    response = await client.post(
        "/api/v1/products",
        headers=ADMIN,
        json={
            "sku": "SM-001",
            "slug": "state-machine-robot",
            "name": "状态机测试机器人",
            "description": "用于状态机测试",
            "base_price": "100.00",
        },
    )
    assert response.status_code == 201
    return response.json()


async def _order(client) -> tuple[dict, dict]:
    """创建并确认报价，下单，返回 (订单, 报价访问头)。"""
    product = await _seed_product(client)
    quote = (
        await client.post(
            "/api/v1/quotes",
            json={
                "customer_name": "测试用户",
                "customer_email": "sm@example.com",
                "items": [{"product_id": product["id"], "quantity": 1}],
            },
        )
    ).json()
    headers = {"X-Quote-Token": quote["access_token"]}
    confirmed = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 1}, headers=headers
    )
    assert confirmed.status_code == 200
    order = (
        await client.post(
            "/api/v1/orders",
            params={"quote_id": quote["id"]},
            headers={**headers, "Idempotency-Key": "sm-order-key-001"},
        )
    ).json()
    return order, headers


async def _transition(client, order_id: int, to_status: str, **extra):
    payload = {"to_status": to_status, **extra}
    return await client.post(f"/api/v1/orders/{order_id}/transition", json=payload, headers=ADMIN)


# --- G-01：鉴权与可达性 -----------------------------------------------------


async def test_transition_requires_admin_token(client):
    """状态迁移是高风险写操作，缺令牌必须拒绝。"""
    order, _ = await _order(client)
    response = await client.post(
        f"/api/v1/orders/{order['id']}/transition", json={"to_status": "PROCESSING"}
    )
    assert response.status_code in (401, 403)
    assert response.json()["error"]["code"] in {"ADMIN_AUTH_REQUIRED", "ADMIN_AUTH_INVALID"}


async def test_transition_route_exists(client):
    """G-01 核心：路由必须真实挂载（此前仅服务层存在）。"""
    order, _ = await _order(client)
    response = await _transition(client, order["id"], "PROCESSING")
    assert response.status_code == 200, "路由应已接线"
    assert response.json()["status"] == "PROCESSING"


async def test_transition_unknown_order_returns_404(client):
    response = await _transition(client, 999999, "PROCESSING")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"


async def test_create_order_exposes_allowed_transitions(client):
    """创建订单响应应给出当前状态的合法迁移目标。"""
    order, _ = await _order(client)
    assert order["status"] == "CREATED"
    assert order["allowed_transitions"] == sorted(ORDER_TRANSITIONS["CREATED"])


# --- G-01：合法迁移矩阵 -----------------------------------------------------


async def test_full_happy_path_to_completed(client):
    """CREATED→PROCESSING→COMPLETED 全链路可达。"""
    order, _ = await _order(client)
    processing = await _transition(client, order["id"], "PROCESSING")
    assert processing.status_code == 200
    assert processing.json()["status"] == "PROCESSING"

    completed = await _transition(client, order["id"], "COMPLETED", reason="履约完成")
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["allowed_transitions"] == [], "COMPLETED 必须是终态"


async def test_cancel_from_created(client):
    """CREATED→CANCELLED 合法。"""
    order, _ = await _order(client)
    response = await _transition(client, order["id"], "CANCELLED", reason="客户取消")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


async def test_failed_then_retry_cycle(client):
    """PROCESSING→FAILED→CREATING 幂等重试路径合法。"""
    order, _ = await _order(client)
    await _transition(client, order["id"], "PROCESSING")
    failed = await _transition(client, order["id"], "FAILED", reason="外部创建失败")
    assert failed.status_code == 200
    retry = await _transition(client, order["id"], "CREATING", reason="幂等重试")
    assert retry.status_code == 200
    assert retry.json()["status"] == "CREATING"


# --- G-01：非法迁移 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("CREATED", "COMPLETED"),  # 跳过 PROCESSING
        ("CREATED", "FAILED"),
        ("CREATED", "CREATING"),
        ("PROCESSING", "CREATED"),  # 回退
        ("PROCESSING", "CREATING"),
        ("COMPLETED", "PROCESSING"),  # 终态迁出
        ("COMPLETED", "CANCELLED"),
        ("CANCELLED", "PROCESSING"),
    ],
)
async def test_invalid_transitions_rejected(client, from_status, to_status):
    """非法迁移一律 409 ORDER_INVALID_TRANSITION，且状态不变。"""
    order, _ = await _order(client)

    # 把订单推进到前提状态。CREATED/COMPLETED 需要经 PROCESSING 中转。
    path = {
        "CREATED": [],
        "PROCESSING": ["PROCESSING"],
        "COMPLETED": ["PROCESSING", "COMPLETED"],
        "CANCELLED": ["CANCELLED"],
    }[from_status]
    for step in path:
        advanced = await _transition(client, order["id"], step)
        assert advanced.status_code == 200, f"前提推进到 {step} 失败"
        assert advanced.json()["status"] == step

    assert to_status not in ORDER_TRANSITIONS[from_status], "用例前提：该迁移应为非法"

    response = await _transition(client, order["id"], to_status)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORDER_INVALID_TRANSITION"

    # 状态必须保持在前提状态，非法迁移不得产生任何副作用
    reread = await _transition(client, order["id"], to_status)
    assert reread.status_code == 409


async def test_unknown_target_status_rejected(client):
    """未知目标状态按非法迁移处理。"""
    order, _ = await _order(client)
    response = await _transition(client, order["id"], "SHIPPED")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORDER_INVALID_TRANSITION"


async def test_transition_rejects_extra_fields(client):
    """迁移请求禁止未声明字段。"""
    order, _ = await _order(client)
    response = await client.post(
        f"/api/v1/orders/{order['id']}/transition",
        json={"to_status": "PROCESSING", "injected": "x"},
        headers=ADMIN,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- G-01：并发保护与审计 ---------------------------------------------------


async def test_expected_status_conflict(client):
    """expected_status 不匹配时拒绝，防止基于过期状态迁移。"""
    order, _ = await _order(client)
    stale = await _transition(client, order["id"], "PROCESSING", expected_status="CANCELLED")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "ORDER_STATUS_CONFLICT"

    fresh = await _transition(client, order["id"], "PROCESSING", expected_status="CREATED")
    assert fresh.status_code == 200


async def test_transition_writes_audit_event(client, session_factory):
    """每次迁移写入 order_events，记录前后状态、原因与操作者。"""
    order, _ = await _order(client)
    await _transition(client, order["id"], "PROCESSING", reason="开始履约")

    async with session_factory() as session:
        events = list(
            await session.scalars(
                select(OrderEvent).where(OrderEvent.order_id == order["id"]).order_by(OrderEvent.id)
            )
        )

    assert [e.to_status for e in events] == ["CREATING", "CREATED", "PROCESSING"]
    assert events[-1].from_status == "CREATED"
    assert events[-1].reason == "开始履约"
    assert events[-1].actor == "admin", "操作者应来自后台身份而非默认 system"


# --- G-03：报价乐观锁 -------------------------------------------------------


async def _quote(client, product_id: int) -> dict:
    return (
        await client.post(
            "/api/v1/quotes",
            json={
                "customer_name": "测试用户",
                "customer_email": "version@example.com",
                "items": [{"product_id": product_id, "quantity": 1}],
            },
        )
    ).json()


async def test_confirm_with_wrong_version_conflicts(client):
    """G-03：version 不匹配必须 409 QUOTE_VERSION_CONFLICT。

    乐观锁要求客户端提交其读到的版本，落后版本不得写入。
    """
    product = await _seed_product(client)
    quote = await _quote(client, product["id"])
    headers = {"X-Quote-Token": quote["access_token"]}

    stale = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 99}, headers=headers
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "QUOTE_VERSION_CONFLICT"

    # 报价必须仍是未确认状态，乐观锁未被绕过
    unchanged = await client.get(f"/api/v1/quotes/{quote['id']}", headers=headers)
    assert unchanged.json()["status"] == "PENDING_CONFIRMATION"

    ok = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 1}, headers=headers
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "CONFIRMED"


async def test_confirm_twice_conflicts(client):
    """已确认的报价再次确认返回版本冲突，不会重复生效。"""
    product = await _seed_product(client)
    quote = await _quote(client, product["id"])
    headers = {"X-Quote-Token": quote["access_token"]}

    first = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 1}, headers=headers
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 1}, headers=headers
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "QUOTE_VERSION_CONFLICT"


async def test_confirm_requires_matching_access_token(client):
    """版本正确但令牌错误仍拒绝，且不泄露报价存在性。"""
    product = await _seed_product(client)
    quote = await _quote(client, product["id"])

    response = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm",
        params={"version": 1},
        headers={"X-Quote-Token": "z" * 64},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "QUOTE_NOT_FOUND"


async def test_confirm_version_must_be_integer(client):
    """version 非整数由参数校验拦截。"""
    product = await _seed_product(client)
    quote = await _quote(client, product["id"])

    response = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm",
        params={"version": "abc"},
        headers={"X-Quote-Token": quote["access_token"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_confirm_expired_wins_over_version(client, session_factory):
    """过期报价即便 version 正确也必须拒绝。"""
    from datetime import datetime, timedelta, timezone

    from app.models.commerce import Quote

    product = await _seed_product(client)
    quote = await _quote(client, product["id"])
    headers = {"X-Quote-Token": quote["access_token"]}

    async with session_factory() as session:
        row = await session.get(Quote, quote["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await session.commit()

    response = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 1}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUOTE_EXPIRED"


async def test_service_transition_rejects_invalid_without_api(client, session_factory):
    """服务层同样受状态机约束（API 之外的调用路径不能绕过）。"""
    order, _ = await _order(client)
    async with session_factory() as session:
        service = CommerceService(session)
        with pytest.raises(Exception) as excinfo:
            await service.transition_order(order["id"], "COMPLETED")  # CREATED 不能直达
        assert getattr(excinfo.value, "detail", {}).get("code") == "ORDER_INVALID_TRANSITION"
