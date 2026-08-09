from fastapi.testclient import TestClient

from apps.api.app.main import app


def test_liveness_and_system_info() -> None:
    client = TestClient(app)
    assert client.get("/health/live").json() == {"status": "ok"}
    response = client.get("/api/v1/system/info")
    assert response.status_code == 200
    assert response.json()["service"] == "nigeria-forest-monitor-api"
