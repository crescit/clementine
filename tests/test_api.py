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
    assert b"ClearPath" in response.content


def test_static_css_available(client: TestClient) -> None:
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    assert b"--bg:" in response.content
