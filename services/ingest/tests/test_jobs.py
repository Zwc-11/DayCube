"""Concurrent requests for one day share a queued or running index job."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import DAY, clone_day
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from ingest.vault import Vault


def test_duplicate_index_requests_share_work_and_allow_retry_after_completion(
    client: TestClient,
    auth: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks: list = []
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda self, fn, *args: tasks.append((fn, args)))
    updates: list[str] = []
    monkeypatch.setattr(client.app.state.store, "update", lambda day: updates.append(day) or {"date": day})

    def submit(_: int) -> str:
        response = client.post(f"/ingest/{DAY}", json={"steps": ["index"]}, headers=auth)
        assert response.status_code == 202
        return response.json()["job_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(submit, range(24)))
    assert len(set(ids)) == 1
    assert len(tasks) == 1
    fn, args = tasks.pop()
    fn(*args)
    assert updates == [DAY]
    assert client.get(f"/jobs/{ids[0]}").json()["status"] == "complete"
    assert submit(0) != ids[0]  # a later request can rebuild again


def test_same_day_reuses_job_at_capacity_and_failed_jobs_can_retry(
    client: TestClient,
    vault: Vault,
    auth: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks: list = []
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda self, fn, *args: tasks.append((fn, args)))
    monkeypatch.setattr("ingest.api.MAX_JOBS", 1)
    other = "2026-10-21"
    clone_day(vault, DAY, other)

    def submit(day: str):
        return client.post(f"/ingest/{day}", json={"steps": ["index"]}, headers=auth)

    first = submit(DAY).json()["job_id"]
    assert submit(DAY).json()["job_id"] == first
    assert submit(other).status_code == 429

    def fail(day: str) -> None:
        raise RuntimeError("synthetic indexing failure")

    monkeypatch.setattr(client.app.state.store, "update", fail)
    fn, args = tasks.pop()
    fn(*args)
    assert client.get(f"/jobs/{first}").json()["status"] == "failed"
    assert submit(DAY).json()["job_id"] != first
    assert len(tasks) == 1
