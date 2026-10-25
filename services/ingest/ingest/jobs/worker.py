"""Running jobs: lease the next one a worker can do, keep its lease alive while it runs, record the
result and queue what follows.

``run_next`` serves the receiver's own threads, one for CPU stages and one for the GPU. Stages
whose code lives elsewhere (the speech worker) lease over HTTP instead (``capture.jobs_api``); the
bookkeeping after a job, ``finish``, is the same for both.
"""

from __future__ import annotations

import threading
from datetime import datetime

from ..vault import Vault
from . import policy
from .stages import STAGES, Permanent, Say, enqueue
from .store import LEASE_S, Job, Jobs


def runnable(needs: str, *, idle_s: float | None, now: datetime | None = None, here: bool = True) -> list[str]:
    """Stages of ``needs`` a worker may take now: ready (their tool installed), and for the GPU only
    while the PC is free. ``here``: only stages with code in this environment."""
    if needs == "gpu" and not policy.free(idle_s, now):
        return []
    return [s.name for s in STAGES.values() if s.needs == needs and (s.run is not None or not here) and s.ready()]


class _Lease(threading.Thread):
    """Extends a job's lease every third of it until stopped."""

    def __init__(self, jobs: Jobs, job: Job, lease_s: float) -> None:
        super().__init__(name=f"lease {job.id}", daemon=True)
        self.jobs, self.job, self.lease_s = jobs, job, lease_s
        self.stopping = threading.Event()
        self.lost = False

    def run(self) -> None:
        while not self.stopping.wait(self.lease_s / 3):
            if not self.jobs.heartbeat(self.job.id, str(self.job.worker), lease_s=self.lease_s):
                self.lost = True
                return

    def stop(self) -> None:
        self.stopping.set()
        self.join()


def finish(jobs: Jobs, job: Job, outputs: dict) -> list[int]:
    """Record a finished job and queue what follows it; the ids of the new jobs."""
    if not jobs.done(job.id, str(job.worker), outputs):
        return []  # the lease ran out meanwhile: the job was re-queued and is someone else's
    new = []
    for name, inputs in STAGES[job.stage].follow(job.inputs, outputs):
        job_id, created = enqueue(jobs, name, inputs)
        if created:
            new.append(job_id)
    return new


def run_next(
    vault: Vault,
    jobs: Jobs,
    worker: str,
    needs: str,
    *,
    idle_s: float | None = None,
    now: datetime | None = None,
    lease_s: float = LEASE_S,
    say: Say = print,
) -> dict | None:
    """Run one job of ``needs`` (``cpu`` or ``gpu``) if one may run; what happened, or None."""
    job = jobs.lease(worker, runnable(needs, idle_s=idle_s, now=now), lease_s=lease_s)
    if job is None:
        return None
    stage = STAGES[job.stage]
    lease = _Lease(jobs, job, lease_s)
    lease.start()
    try:
        outputs = stage.run(vault, job.inputs, say)  # type: ignore[misc]
    except Permanent as error:
        lease.stop()
        jobs.fail(job.id, worker, str(error), retry=False)
        return {"job": job.id, "stage": job.stage, "inputs": job.inputs, "did": "failed", "error": str(error)}
    except Exception as error:  # noqa: BLE001 - recorded on the job, retried by the store's rules
        lease.stop()
        reason = f"{type(error).__name__}: {error}"
        status = jobs.fail(job.id, worker, reason, max_attempts=stage.attempts)
        return {"job": job.id, "stage": job.stage, "inputs": job.inputs, "did": "failed" if status == "failed" else "retry",
                "error": reason}  # fmt: skip
    lease.stop()
    follows = finish(jobs, job, outputs)
    return {
        "job": job.id,
        "stage": job.stage,
        "inputs": job.inputs,
        "did": "done",
        "outputs": outputs,
        "queued": follows,
    }
