# Jobs

Work the vault needs done after capture (a scan's 3D now, speech and meshes next) runs as jobs.
They are queued in `vault/jobs.sqlite` and run by the capture receiver with no command.

## Stages (`ingest/jobs/stages.py`)

| Stage | Needs | Version | Runs | Leads to |
| --- | --- | --- | --- | --- |
| `splat_preview` | CPU | 1 | the scan's training set and LiDAR preview (`ingest/splat`, a minute or two) | `splat_train` |
| `splat_train` | GPU | 1 | Brush on the RTX 5070 (tens of minutes); ready once Brush is installed | |
| `scan_mesh` | CPU | 1 | the scan's mesh (`ingest/splat/mesh.py`); ready once Open3D is installed | |
| `conversations` | CPU | 1 | the speech worker finds the talks in an hour of the app's sound; the receiver makes them talk moments | `speech` for each |
| `speech` | GPU | 1 | the speech worker, in its own environment, leased over HTTP ([speech.md](speech.md)) | |

- Every stage has a name, a version, what it needs, its code, and what follows it.
- A stage with no code here (`run=None`) is run by a worker in another environment, such as the
  speech worker, which leases jobs over HTTP.
- Raising a stage's version makes its jobs new, so they run again.

## The queue (`ingest/jobs/store.py`)

- **One job per input.** A job is one stage on one input. Its key is the hash of the stage, the
  version and the input, so queueing the same job twice does nothing. A job that is done or
  failed is not queued again by discovery.
- **Leases.** A worker leases a job for 2 minutes and extends the lease every 40 seconds while it
  runs. If the lease runs out (the worker died), the job goes back to the queue and the attempt is
  counted. A late result from that worker is refused.
- **Failures.** A failure is retried after 1 minute, then 4 minutes. After the stage's attempts
  (3, or 2 for `splat_train`) the job is `failed`, with the error kept.
- **Permanent failures.** A failure that a retry would only repeat, such as a scan whose poses
  disagree with its depth, is `failed` at once.
- **Order.** Highest priority first, then oldest.

## Who runs them

- **The receiver (`capture/jobs.py`).** One thread runs CPU stages and one runs GPU stages.
  - The GPU thread takes a job only while the PC is free: no keyboard or mouse input for 10 minutes,
    or between 01:00 and 07:00 (`ingest/jobs/policy.py`).
  - Every minute the CPU thread looks for what the vault still needs (`discover`): a scan's 3D,
    a recording's speech. Both threads run jobs until none is left.
  - When `services/speech` is installed, the receiver also runs the speech worker and restarts it
    if it stops.
- **Workers elsewhere.** These use the receiver's API, with the vault's bearer token:
  - `POST /jobs/lease {worker, needs: cpu|gpu, stages?}` returns `{job}` or `{job: null}`. It returns
    null when nothing may run now, for example a GPU job while you are at the PC.
  - `POST /jobs/{id}/heartbeat {worker}` returns `{ok}`. `ok` is false once the lease is lost.
  - `POST /jobs/{id}/done {worker, outputs}` returns `{ok, queued}`, where `queued` lists what
    follows the job.
  - `POST /jobs/{id}/fail {worker, error, retry}` returns `{status}`.
  - `GET /jobs/status`.
- **One process per scan.** Making a scan's 3D holds an operating-system lock on
  `vault/derived/splat/<date>/<moment>/.lock`. A second run on the same scan, such as `daycube splat`
  while the receiver trains it, stops with "being made by another process" instead of overwriting
  its frames.

## Commands

    uv run daycube jobs                      # what waits, runs and failed (after looking for new work)
    uv run daycube jobs retry --stage splat_train
    uv run daycube jobs run                  # run the CPU jobs here, until none is left
    uv run daycube jobs run --needs gpu --now

`.\capture doctor` shows one `jobs` row for what runs, one per stage with waiting jobs (and when
they will run), and one per failure with its reason and the retry command.

## Tests

`services/ingest/tests/test_job_store.py` covers:

- keys and versions
- priority order and heartbeats
- a lease that runs out, counted until the job fails
- the back-off and retry
- what follows a job
- GPU jobs waiting for a free PC
- a permanent failure
- a worker leasing over HTTP

`test_splat.py` runs a scan through discovery, its preview and its training, with a stand-in for
Brush.
