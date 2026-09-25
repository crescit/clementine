"""API smoke tests for K0 bootstrap. Domain endpoint suites land in K5–K6."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_serves_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert b"Review Queue" in response.content


def test_static_pages_available(client: TestClient) -> None:
    for page in ["index.html", "submission.html", "submit.html"]:
        response = client.get(f"/static/{page}")
        assert response.status_code == 200, f"{page} failed"
        assert "text/html" in response.headers["content-type"], f"{page} not html"
    index = client.get("/static/index.html").content
    assert b"queue.js" in index
    # Each page wires its own native module script (queue.js / submission.js / submit.js).
    assert b"type=\"module\"" in index
    assert b"submission.js" in client.get("/static/submission.html").content
    assert b"submit.js" in client.get("/static/submit.html").content
    # All pages import the shared common.js seam.
    for page in ["queue.js", "submission.js", "submit.js"]:
        assert b"common.js" in client.get(f"/static/{page}").content


def test_static_assets_available(client: TestClient) -> None:
    for asset in [
        "styles.css",
        "common.js",
        "queue.js",
        "submission.js",
        "submit.js",
    ]:
        response = client.get(f"/static/{asset}")
        assert response.status_code == 200, f"{asset} failed"
