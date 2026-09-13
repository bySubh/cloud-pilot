from fastapi.testclient import TestClient


def _client(tmp_workspace):
    # Import after the autouse fixture + tmp_workspace fixture have patched
    # config/env so app.main picks up an isolated, mocked environment.
    from app.main import app

    return TestClient(app)


def test_health_endpoint(tmp_workspace):
    with _client(tmp_workspace) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_post_runs_returns_run_id_and_queued_or_terminal_status(tmp_workspace):
    with _client(tmp_workspace) as client:
        response = client.post(
            "/runs",
            json={
                "request": "Create infrastructure for a web application needing PostgreSQL and object storage",
                "constraints": "use secure defaults",
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert "run_id" in body
        assert body["status"] in ("queued", "running", "success", "failed")


def test_get_run_after_creation(tmp_workspace):
    with _client(tmp_workspace) as client:
        create_response = client.post("/runs", json={"request": "Create a secure storage bucket"})
        run_id = create_response.json()["run_id"]

        get_response = client.get(f"/runs/{run_id}")
        assert get_response.status_code == 200
        body = get_response.json()
        assert body["run_id"] == run_id
        assert body["status"] in ("queued", "running", "success", "failed")
        assert isinstance(body["resources"], list)


def test_get_run_status_endpoint(tmp_workspace):
    with _client(tmp_workspace) as client:
        create_response = client.post("/runs", json={"request": "Create a secure storage bucket"})
        run_id = create_response.json()["run_id"]

        status_response = client.get(f"/runs/{run_id}/status")
        assert status_response.status_code == 200
        assert status_response.json()["run_id"] == run_id


def test_get_nonexistent_run_returns_404(tmp_workspace):
    with _client(tmp_workspace) as client:
        response = client.get("/runs/does-not-exist")
        assert response.status_code == 404


def test_list_runs_endpoint(tmp_workspace):
    with _client(tmp_workspace) as client:
        client.post("/runs", json={"request": "Create a secure storage bucket"})
        response = client.get("/runs")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


def test_malformed_run_request_returns_422(tmp_workspace):
    with _client(tmp_workspace) as client:
        response = client.post("/runs", json={"request": "ab"})  # below min_length=3... actually "ab" len 2
        assert response.status_code == 422
