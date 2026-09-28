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
    assert "innerHTML" not in script.text


def test_frontend_source_files_exist():
    root = Path(__file__).parents[1]
    assert (root / "frontend" / "index.html").is_file()
    assert (root / "frontend" / "styles.css").is_file()
    assert (root / "frontend" / "app.js").is_file()
