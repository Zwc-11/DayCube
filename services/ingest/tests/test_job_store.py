"""The vault's job queue (ingest.jobs): one job per stage, version and input; leases that run out;
retries with back-off; permanent failures; what follows a job; the GPU only when the PC is free;
and workers outside the receiver leasing over HTTP."""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.config import Settings
from ingest.jobs import stages, worker
from ingest.jobs.store import BACKOFF_S, MAX_ATTEMPTS, Jobs
from ingest.vault import Vault

T0 = 1_800_000_000.0
AWAY = 3600.0  # seconds since the last input
DAYTIME = datetime(2026, 10, 25, 14, 0)


@pytest.fixture
def jobs(tmp_path: Path) -> Jobs:
    store = Jobs(tmp_path / "jobs.sqlite")
    yield store
    store.close()


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand-in stages: ``a`` (cpu) leads to ``b`` (gpu); ``broken`` fails, ``hopeless`` for good;
    ``speech`` has its code elsewhere."""
    ran: list[str] = []
    table = {
        "a": stages.Stage("a", 1, "cpu", lambda v, i, s: ran.append(f"a{i['n']}") or {"n2": i["n"] * 2},
                          follow=lambda i, o: [("b", {"n": o["n2"]})]),
        "b": stages.Stage("b", 1, "gpu", lambda v, i, s: ran.append(f"b{i['n']}") or {}),
        "broken": stages.Stage("broken", 1, "cpu", lambda v, i, s: 1 / 0),
        "hopeless": stages.Stage("hopeless", 1, "cpu", lambda v, i, s: (_ for _ in ()).throw(stages.Permanent("never"))),
        "speech": stages.Stage("speech", 3, "gpu", None),
    }  # fmt: skip
    monkeypatch.setattr(stages, "STAGES", table)
    monkeypatch.setattr(worker, "STAGES", table)
    return ran


def test_a_job_is_added_once_and_a_new_version_is_a_new_job(jobs: Jobs) -> None:
    first, new = jobs.add("splat_train", 1, {"date": "2026-10-24", "moment": "mo_scan_x"}, now=T0)
    assert new
    assert jobs.add("splat_train", 1, {"moment": "mo_scan_x", "date": "2026-10-24"}, now=T0 + 5) == (first, False)
    second, new = jobs.add("splat_train", 2, {"date": "2026-10-24", "moment": "mo_scan_x"}, now=T0)
    assert new and second != first
    assert [j["version"] for j in jobs.find("splat_train", {"date": "2026-10-24", "moment": "mo_scan_x"})] == [2, 1]


def test_jobs_are_leased_by_priority_then_age_and_kept_alive_by_heartbeats(jobs: Jobs) -> None:
    old, _ = jobs.add("a", 1, {"n": 1}, now=T0)
    urgent, _ = jobs.add("a", 1, {"n": 2}, priority=5, now=T0 + 1)
    jobs.add("other", 1, {"n": 3}, now=T0)
    assert jobs.lease("w1", ["a"], lease_s=60, now=T0 + 2).id == urgent
    assert jobs.lease("w2", ["a"], lease_s=60, now=T0 + 2).id == old
    assert jobs.lease("w3", ["a"], now=T0 + 2) is None
    assert jobs.heartbeat(old, "w2", lease_s=60, now=T0 + 50)
    assert not jobs.heartbeat(old, "w1", now=T0 + 50)  # not w1's job
    # w1 sent no heartbeat: its job comes back; w2's lease was extended to T0 + 110
    again = jobs.lease("w3", ["a"], now=T0 + 100)
    assert again is not None and (again.id, again.attempts) == (urgent, 1)
    assert jobs.lease("w4", ["a"], now=T0 + 100) is None
    assert jobs.done(old, "w2", {"ok": 1}, now=T0 + 101)
    assert jobs.get(old)["status"] == "done" and jobs.get(old)["outputs"] == {"ok": 1}


def test_a_lease_that_runs_out_puts_the_job_back_and_counts_as_an_attempt(jobs: Jobs) -> None:
    job_id, _ = jobs.add("a", 1, {"n": 1}, now=T0)
    now = T0
    for attempt in range(MAX_ATTEMPTS):
        job = jobs.lease(f"w{attempt}", ["a"], lease_s=60, now=now)
        assert job is not None and job.attempts == attempt
        now += 61  # the worker died
    assert jobs.lease("late", ["a"], now=now) is None
    row = jobs.get(job_id)
    assert row["status"] == "failed" and row["attempts"] == MAX_ATTEMPTS and "w2 stopped answering" in row["error"]
    assert not jobs.done(job_id, "w2", {}, now=now)  # a dead worker's late result is refused


def test_failures_back_off_then_fail_and_retry_queues_them_again(jobs: Jobs) -> None:
    job_id, _ = jobs.add("a", 1, {"n": 1}, now=T0)
    jobs.lease("w", ["a"], now=T0)
    assert jobs.fail(job_id, "w", "disk full", now=T0) == "queued"
    assert jobs.lease("w", ["a"], now=T0 + BACKOFF_S - 1) is None  # backing off
    jobs.lease("w", ["a"], now=T0 + BACKOFF_S)
    assert jobs.fail(job_id, "w", "disk full", now=T0 + BACKOFF_S) == "queued"
    assert jobs.lease("w", ["a"], now=T0 + BACKOFF_S * 4) is None  # 4x longer
    jobs.lease("w", ["a"], now=T0 + BACKOFF_S * 5)
    assert jobs.fail(job_id, "w", "disk full", now=T0 + BACKOFF_S * 5) == "failed"
    state = jobs.status(now=T0 + BACKOFF_S * 5)
    assert state["stages"]["a"]["failed"] == 1 and state["failed"][0]["error"] == "disk full"
    assert jobs.retry(stage="a") == 1
    assert jobs.lease("w", ["a"], now=T0 + BACKOFF_S * 5).attempts == 0


def test_the_worker_runs_a_job_queues_what_follows_and_waits_for_a_free_pc_for_the_gpu(
    jobs: Jobs, vault: Vault, graph: list[str]
) -> None:
    stages.enqueue(jobs, "a", {"n": 3})
    result = worker.run_next(vault, jobs, "w", "cpu", say=lambda _: None)
    assert result["did"] == "done" and graph == ["a3"] and len(result["queued"]) == 1
    assert worker.run_next(vault, jobs, "w", "cpu") is None  # b needs the GPU
    assert worker.run_next(vault, jobs, "w", "gpu", idle_s=60, now=DAYTIME) is None  # you are at the PC
    assert worker.run_next(vault, jobs, "w", "gpu", idle_s=AWAY, now=DAYTIME)["did"] == "done"
    assert graph == ["a3", "b6"]
    assert stages.enqueue(jobs, "a", {"n": 3})[1] is False  # done once: not again


def test_a_failing_stage_is_retried_and_a_permanent_failure_is_not(jobs: Jobs, vault: Vault, graph: list[str]) -> None:
    broken, _ = stages.enqueue(jobs, "broken", {})
    hopeless, _ = stages.enqueue(jobs, "hopeless", {})
    first = worker.run_next(vault, jobs, "w", "cpu")
    second = worker.run_next(vault, jobs, "w", "cpu")
    by_stage = {r["stage"]: r for r in (first, second)}
    assert by_stage["broken"]["did"] == "retry" and "ZeroDivisionError" in by_stage["broken"]["error"]
    assert by_stage["hopeless"]["did"] == "failed" and by_stage["hopeless"]["error"] == "never"
    assert jobs.get(broken)["status"] == "queued" and jobs.get(hopeless)["status"] == "failed"


def test_a_worker_elsewhere_leases_over_http_and_what_follows_is_queued(
    jobs: Jobs, vault: Vault, graph: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from capture import api as capture_api
    from capture.api import create_capture_app

    monkeypatch.setattr(capture_api, "idle_seconds", lambda: AWAY)
    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    auth = {"Authorization": f"Bearer {vault.token}"}
    with TestClient(create_capture_app(settings)) as client:
        store = client.app.state.jobs
        stages.enqueue(store, "speech", {"memo": "m1"})
        stages.enqueue(store, "a", {"n": 1})
        assert client.post("/jobs/lease", json={"worker": "speech@pc", "needs": "gpu"}).status_code == 401
        body = client.post(
            "/jobs/lease", json={"worker": "speech@pc", "needs": "gpu", "stages": ["speech"]}, headers=auth
        ).json()
        job = body["job"]
        assert job["stage"] == "speech" and job["version"] == 3 and job["inputs"] == {"memo": "m1"}
        assert client.post(f"/jobs/{job['id']}/heartbeat", json={"worker": "speech@pc"}, headers=auth).json() == {
            "ok": True
        }
        assert client.post(f"/jobs/{job['id']}/heartbeat", json={"worker": "other"}, headers=auth).json() == {
            "ok": False
        }
        done = client.post(
            f"/jobs/{job['id']}/done", json={"worker": "speech@pc", "outputs": {"turns": 12}}, headers=auth
        )
        assert done.json() == {"ok": True, "queued": []}
        assert store.get(job["id"])["outputs"] == {"turns": 12}
        # Code-in-the-receiver stages are leased over HTTP too, if asked for; what follows is queued here.
        cpu = client.post("/jobs/lease", json={"worker": "x", "needs": "cpu"}, headers=auth).json()["job"]
        assert cpu["stage"] == "a"
        assert (
            len(
                client.post(f"/jobs/{cpu['id']}/done", json={"worker": "x", "outputs": {"n2": 2}}, headers=auth).json()[
                    "queued"
                ]
            )
            == 1
        )
        state = client.get("/jobs/status", headers=auth).json()
        assert state["stages"]["b"]["queued"] == 1 and state["stages"]["speech"]["done"] == 1
        failing = stages.enqueue(store, "speech", {"memo": "m2"})[0]
        client.post("/jobs/lease", json={"worker": "speech@pc", "needs": "gpu", "stages": ["speech"]}, headers=auth)
        assert client.post(
            f"/jobs/{failing}/fail", json={"worker": "speech@pc", "error": "CUDA OOM"}, headers=auth
        ).json() == {"status": "queued"}
        # At the PC: no GPU job is handed out.
        monkeypatch.setattr(capture_api, "idle_seconds", lambda: 5.0)
        monkeypatch.setattr("ingest.jobs.policy.NIGHT", (0, 0))
        assert client.post("/jobs/lease", json={"worker": "s", "needs": "gpu"}, headers=auth).json() == {"job": None}
