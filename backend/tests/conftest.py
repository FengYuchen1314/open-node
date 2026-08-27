from fastapi import FastAPI
from fastapi.testclient import TestClient

ADMIN_PASSWORD = "test-operator-password-only"


def authenticated_client(app: FastAPI) -> TestClient:
    if not app.state.auth.configured():
        app.state.auth.set_administrator("admin", ADMIN_PASSWORD)
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
