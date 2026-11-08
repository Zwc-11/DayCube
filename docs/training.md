# Training runs

The Training page (graph page › the activity icon, or `?view=training`; in the desktop app the same)
shows every model trained on this PC: one chart per metric against the step, refreshed every 5 s
while a run is under way. A run is a folder in `vault/training/<id>/`:

| File | What it holds |
| --- | --- |
| `run.json` | The `training` contract (`packages/schemas/training.schema.json`): model, title, `started`/`ended`, `status` (`running`, `done`, `failed`), `steps_total`, the `metrics` with their unit and which way is better (`higher`, `lower` or null), `device`, `source`, final `result`, `error` |
| `metrics.jsonl` | One line per logged step: `{"step": n, "t": "<ISO time>", "<metric>": number, ...}`; a line may carry some of the metrics only |

From Python in DayCube's environments, `ingest.training.Run` writes both:

```python
from ingest.training import Run

with Run(vault_root, "revisit-predictor", "Revisit predictor, AEA sites 1-4", steps_total=20_000,
         metrics={"loss": ("L1", "lower"), "val_loss": ("L1", "lower")}, device="RTX 5070") as run:
    for step in range(20_000):
        ...
        run.log(step, loss=loss)          # unknown names and non-finite values are left out
    run.result["val_loss"] = best          # saved when the run ends
```

Leaving the `with` block marks the run `done`; an exception marks it `failed` with the error. Any
other trainer can write the two files directly. A run left `running` whose files stop changing for
15 minutes is shown as stalled: its process ended without saying so.

Written today by: a scan's splat training (`ingest/splat/run.py`: held-out PSNR at each evaluation
and the splat count). The eye tracker's learning from clicks is listed from its own log
(`vault/calibration/learning.jsonl`): one run for the current calibration, each fit a step, with
the held-out error of the candidate (`learned_px`), the model in use and the calibration alone, and
the clicks it learned from. Its full dashboard stays under Settings › Eye tracking.
