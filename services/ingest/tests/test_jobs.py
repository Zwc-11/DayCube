from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from ingest.jobs import IndexJobs, JobsFull
from ingest.vault import Record

DAY = "2026-10-20"


def test_concurrent_duplicates_have_one_scheduling_owner_and_one_build() -> None:
    builds = []

    def build(date: str) -> Record:
        builds.append(date)
        return {"units": 28}

    jobs = IndexJobs(build)
    barrier = Barrier(16)

    def submit(_: int) -> tuple[str, bool]:
        barrier.wait(timeout=5)
        return jobs.submit(DAY, ("version-1",))

    with ThreadPoolExecutor(max_workers=16) as pool:
        submissions = list(pool.map(submit, range(16)))
        identifier = submissions[0][0]
        assert len({row[0] for row in submissions}) == 1
        assert sum(row[1] for row in submissions) == 1
        list(pool.map(jobs.run, [identifier] * 16))
    assert builds == [DAY]
    assert jobs.snapshot(identifier)["status"] == "complete"


def test_concurrent_distinct_admission_obeys_capacity() -> None:
    jobs = IndexJobs(lambda _: {}, max_active=4)
    barrier = Barrier(16)

    def submit(index: int) -> str | None:
        barrier.wait(timeout=5)
        try:
            return jobs.submit(DAY, (index,))[0]
        except JobsFull:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(submit, range(16)))
    assert sum(identifier is not None for identifier in results) == 4


def test_running_builder_does_not_hold_registry_lock() -> None:
    started, finish = Event(), Event()

    def build(_: str) -> Record:
        started.set()
        assert finish.wait(timeout=5)
        return {"units": 28}

    jobs = IndexJobs(build, max_active=1)
    identifier, _ = jobs.submit(DAY, ("v1",))
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(jobs.run, identifier)
        try:
            assert started.wait(timeout=5)
            assert pool.submit(jobs.snapshot, identifier).result(timeout=5)["status"] == "running"
            assert pool.submit(jobs.submit, DAY, ("v1",)).result(timeout=5) == (identifier, False)
            with pytest.raises(JobsFull):
                pool.submit(jobs.submit, DAY, ("v2",)).result(timeout=5)
        finally:
            finish.set()
        running.result(timeout=5)
    assert jobs.submit(DAY, ("v2",))[1]


def test_source_changes_and_completed_jobs_allow_new_rebuilds() -> None:
    jobs = IndexJobs(lambda _: {})
    original, _ = jobs.submit(DAY, ("v1",))
    changed, scheduled = jobs.submit(DAY, ("v2",))
    assert scheduled and changed != original
    assert jobs.submit(DAY, ("v1",)) == (original, False)
    jobs.run(original)
    retry, scheduled = jobs.submit(DAY, ("v1",))
    assert scheduled and retry != original
    assert jobs.snapshot(original)["status"] == "complete"


def test_failed_build_releases_capacity_and_does_not_leak_diagnostics() -> None:
    def fail(_: str) -> Record:
        raise RuntimeError("internal failure detail")

    jobs = IndexJobs(fail, max_active=1)
    identifier, _ = jobs.submit(DAY, ())
    jobs.run(identifier)
    state = jobs.snapshot(identifier)
    assert state["status"] == "failed"
    assert "internal failure detail" not in state["message"]
    assert jobs.submit(DAY, ())[1]


def test_history_eviction_preserves_active_jobs_even_with_reused_keys() -> None:
    jobs = IndexJobs(lambda _: {}, max_entries=3, max_active=2)
    first, _ = jobs.submit(DAY, ())
    jobs.run(first)
    second, _ = jobs.submit(DAY, ())
    jobs.run(second)
    active, _ = jobs.submit(DAY, ())
    other, _ = jobs.submit("2026-10-21", ())
    assert jobs.snapshot(first) is None
    assert jobs.snapshot(second)["status"] == "complete"
    assert jobs.snapshot(active)["status"] == "queued"
    assert jobs.snapshot(other)["status"] == "queued"


def test_status_and_nested_results_are_detached_from_callers() -> None:
    result = {"units": 28}
    jobs = IndexJobs(lambda _: result)
    identifier, _ = jobs.submit(DAY, ())
    queued = jobs.snapshot(identifier)
    queued["status"] = "complete"
    assert jobs.snapshot(identifier)["status"] == "queued"
    jobs.run(identifier)
    result["units"] = 999
    returned = jobs.snapshot(identifier)
    returned["result"]["units"] = 888
    assert jobs.snapshot(identifier)["result"]["units"] == 28
    assert jobs.snapshot("unknown") is None
    jobs.run("unknown")


@pytest.mark.parametrize(("active", "entries"), [(0, 100), (3, 2), (1, 0)])
def test_invalid_capacity_fails_at_construction(active: int, entries: int) -> None:
    with pytest.raises(ValueError):
        IndexJobs(lambda _: {}, max_active=active, max_entries=entries)
