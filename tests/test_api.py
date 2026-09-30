from decimal import Decimal


async def seed_product(client):
    response = await client.post(
        "/api/v1/products",
        json={
            "sku": "TEST-001",
            "slug": "test-robot",
            "name": "测试巡检机器人",
            "description": "适合仓储巡检和目标识别",
            "base_price": "1000.00",
            "capabilities": ["目标识别"],
            "use_cases": ["巡检"],
            "specs": {"battery_hours": 4},
        },
    )
    assert response.status_code == 201
    return response.json()


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_product_search_and_assistant(client):
    product = await seed_product(client)
    response = await client.get("/api/v1/products", params={"query": "巡检"})
    assert response.status_code == 200
    assert response.json()["total"] == 1

    response = await client.post("/api/v1/assistant/messages", json={"message": "我需要巡检机器人"})
    assert response.status_code == 200
    assert response.json()["recommendations"][0]["product_id"] == product["id"]


async def test_quote_confirm_and_idempotent_order(client):
    product = await seed_product(client)
    quote_response = await client.post(
        "/api/v1/quotes",
        json={
            "customer_name": "张三",
            "customer_email": "zhang@example.com",
            "items": [{"product_id": product["id"], "quantity": 2}],
        },
    )
    assert quote_response.status_code == 201
    quote = quote_response.json()
    assert Decimal(quote["total"]) == Decimal("2000.00")
    access = {"X-Quote-Token": quote["access_token"]}

    confirm = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 1}, headers=access
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "CONFIRMED"

    first = await client.post(
        "/api/v1/orders",
        params={"quote_id": quote["id"]},
        headers={**access, "Idempotency-Key": "quote-order-001"},
    )
    second = await client.post(
        "/api/v1/orders",
        params={"quote_id": quote["id"]},
        headers={**access, "Idempotency-Key": "quote-order-001"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


async def test_quote_with_multiple_products_uses_server_prices(client):
    first = await seed_product(client)
    second = await client.post(
        "/api/v1/products",
        json={
            "sku": "TEST-002",
            "slug": "test-robot-two",
            "name": "测试教育机器人",
            "description": "用于课程演示",
            "base_price": "2500.00",
            "use_cases": ["教育"],
        },
    )
    assert second.status_code == 201
    quote = await client.post(
        "/api/v1/quotes",
        json={
            "customer_name": "测试用户",
            "customer_email": "demo@example.com",
            "items": [
                {"product_id": first["id"], "quantity": 2},
                {"product_id": second.json()["id"], "quantity": 3},
            ],
        },
    )
    assert quote.status_code == 201
    assert len(quote.json()["items"]) == 2
    assert Decimal(quote.json()["total"]) == Decimal("9500.00")


async def test_quote_rejects_client_tax_and_mixed_currency(client):
    product = await seed_product(client)
    payload = {
        "customer_name": "李四",
        "customer_email": "li@example.com",
        "items": [{"product_id": product["id"], "quantity": 1}],
    }
    tampered = await client.post("/api/v1/quotes", json={**payload, "tax_rate": "0.5"})
    assert tampered.status_code == 422

    other = await client.post(
        "/api/v1/products",
        json={
            "sku": "USD-001",
            "slug": "usd-robot",
            "name": "USD Robot",
            "description": "USD-priced robot",
            "currency": "USD",
            "base_price": "500.00",
        },
    )
    assert other.status_code == 201
    payload["items"].append({"product_id": other.json()["id"], "quantity": 1})
    mixed = await client.post("/api/v1/quotes", json=payload)
    assert mixed.status_code == 422
    assert mixed.json()["detail"]["code"] == "MIXED_CURRENCY"


async def test_idempotency_key_cannot_be_reused_for_another_quote(client):
    product = await seed_product(client)
    quotes = []
    for email in ("first@example.com", "second@example.com"):
        response = await client.post(
            "/api/v1/quotes",
            json={
                "customer_name": "测试用户",
                "customer_email": email,
                "items": [{"product_id": product["id"], "quantity": 1}],
            },
        )
        assert response.status_code == 201
        quote_id = response.json()["id"]
        quotes.append(response.json())
        confirmed = await client.post(
            f"/api/v1/quotes/{quote_id}/confirm",
            params={"version": 1},
            headers={"X-Quote-Token": response.json()["access_token"]},
        )
        assert confirmed.status_code == 200

    headers = {"Idempotency-Key": "shared-order-key"}
    first = await client.post(
        "/api/v1/orders",
        params={"quote_id": quotes[0]["id"]},
        headers={**headers, "X-Quote-Token": quotes[0]["access_token"]},
    )
    second = await client.post(
        "/api/v1/orders",
        params={"quote_id": quotes[1]["id"]},
        headers={**headers, "X-Quote-Token": quotes[1]["access_token"]},
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_quote_and_order_require_matching_access_token(client):
    product = await seed_product(client)
    response = await client.post(
        "/api/v1/quotes",
        json={
            "customer_name": "测试用户",
            "customer_email": "test@example.com",
            "items": [{"product_id": product["id"], "quantity": 1}],
        },
    )
    quote = response.json()
    assert (await client.get(f"/api/v1/quotes/{quote['id']}")).status_code == 422
    wrong = {"X-Quote-Token": "x" * 64}
    assert (await client.get(f"/api/v1/quotes/{quote['id']}", headers=wrong)).status_code == 404
    denied = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm", params={"version": 1}, headers=wrong
    )
    assert denied.status_code == 404
    allowed = await client.get(
        f"/api/v1/quotes/{quote['id']}", headers={"X-Quote-Token": quote["access_token"]}
    )
    assert allowed.status_code == 200
    confirmed = await client.post(
        f"/api/v1/quotes/{quote['id']}/confirm",
        params={"version": 1},
        headers={"X-Quote-Token": quote["access_token"]},
    )
    assert confirmed.status_code == 200
    order = await client.post(
        "/api/v1/orders",
        params={"quote_id": quote["id"]},
        headers={"Idempotency-Key": "test-access-key", "X-Quote-Token": quote["access_token"]},
    )
    assert order.status_code == 201
    denied_order = await client.get(f"/api/v1/orders/{order.json()['id']}", headers=wrong)
    assert denied_order.status_code == 404
    allowed_order = await client.get(
        f"/api/v1/orders/{order.json()['id']}",
        headers={"X-Quote-Token": quote["access_token"]},
    )
    assert allowed_order.status_code == 200
