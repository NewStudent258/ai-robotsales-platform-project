from pathlib import Path


async def test_frontend_home_and_static_assets(client):
    home = await client.get("/")
    assert home.status_code == 200
    assert "ROBOTIQ" in home.text

    styles = await client.get("/static/styles.css")
    script = await client.get("/static/app.js")
    assert styles.status_code == 200
    assert script.status_code == 200
    assert "assistant-panel" in styles.text
    assert "/api/v1/assistant/messages" in script.text
    assert "/api/v1/quotes" in script.text
    assert "Idempotency-Key" in script.text
    # P0：助手生成的报价必须能接入确认流程，且令牌按需换取而非随对话回流。
    assert "/api/v1/assistant/quote-token" in script.text
    assert "ensureQuoteToken" in script.text
    assert "reviewAssistantQuote" in script.text
    # P1：金额构成与版本链必须展示，修改配置走 /revise 且带乐观锁。
    assert "renderQuoteTotals" in script.text
    assert "/revise" in script.text
    assert "expected_version" in script.text
    assert "quote-discount" in script.text
    assert "applied_rules" in script.text
    assert "innerHTML" not in script.text

    # P1 新增的金额与版本展示节点必须真实存在于页面中，否则脚本会静默失效。
    assert 'id="quote-discount"' in home.text
    assert 'id="quote-rules"' in home.text
    assert 'id="quote-versions"' in home.text

    sales = await client.get("/sales")
    assert sales.status_code == 200
    assert "ROBOTIQ SALES FLOOR" in sales.text

    sales_script = await client.get("/static/sales.js")
    sales_styles = await client.get("/static/sales.css")
    assert sales_script.status_code == 200
    assert sales_styles.status_code == 200
    assert "/api/v1/products" in sales_script.text
    assert "/api/v1/quotes" in sales_script.text
    assert "innerHTML" not in sales_script.text


def test_frontend_source_files_exist():
    root = Path(__file__).parents[1]
    assert (root / "frontend" / "index.html").is_file()
    assert (root / "frontend" / "styles.css").is_file()
    assert (root / "frontend" / "app.js").is_file()
