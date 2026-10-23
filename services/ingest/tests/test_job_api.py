from fastapi.testclient import TestClient
from ingest.jobs import IndexJobs
from ingest.vault import Vault
from pytest import MonkeyPatch

DAY = "2026-10-20"


def test_pending_requests_share_a_job_until_sources_change(
    client: TestClient, auth: dict[str, str], monkeypatch: MonkeyPatch
) -> None:
    scheduled = []

    def defer_run(_: IndexJobs, identifier: str) -> None:
        scheduled.append(identifier)

    monkeypatch.setattr(IndexJobs, "run", defer_run)
    first = client.post(f"/ingest/{DAY}", json={"steps": ["index"]}, headers=auth)
    second = client.post(f"/ingest/{DAY}", json={"steps": ["index"]}, headers=auth)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(scheduled) == 1
    store: Vault = client.app.state.vault
    store.write_json(store.root / "places.json", {"hall": "New name"})
    changed = client.post(f"/ingest/{DAY}", json={"steps": ["index"]}, headers=auth)
    assert changed.status_code == 202
    assert changed.json() != first.json()
    assert len(scheduled) == 2
    state = client.get(f"/jobs/{first.json()['job_id']}").json()
    assert state["status"] == "queued"
    assert client.get("/jobs/unknown").status_code == 404


def test_admission_rejects_excess_distinct_jobs_with_retry_header(
    client: TestClient, auth: dict[str, str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(IndexJobs, "run", lambda *_: None)
    store: Vault = client.app.state.vault
    for version in range(8):
        store.write_json(store.root / "places.json", {"hall": f"Version {version}"})
        assert client.post(f"/ingest/{DAY}", json={"steps": ["index"]}, headers=auth).status_code == 202
    store.write_json(store.root / "places.json", {"hall": "Excess version"})
    response = client.post(f"/ingest/{DAY}", json={"steps": ["index"]}, headers=auth)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
