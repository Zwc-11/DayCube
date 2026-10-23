"""Tables and a figure from eval/results/memory-sim.json (written by eval/memory_sim.py)."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

import numpy as np

QUERIES = ("all", "rare", "changed", "recent")


def group(rows: list[dict], *keys: str) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        out[tuple(row[k] if k in row else row["life"][k] for k in keys)].append(row)
    return out


def stat(rows: list[dict], field: str) -> tuple[float, float]:
    values = np.array([r[field] for r in rows])
    return float(values.mean()), float(values.std(ddof=1)) if len(values) > 1 else 0.0


def main_table(rows: list[dict]) -> list[str]:
    lines = []
    for (life,), life_rows in group(rows, "name").items():
        share, _ = stat([r for r in life_rows if r["policy"] == "oracle"], "novelty_share")
        lines.append(f"\n## {life}: novelty share {share:.3f} (distinct places + changes seen, over visits)")
        shares = sorted({round(r["budget"] / r["visits"], 2) for r in life_rows})
        for q in QUERIES:
            lines.append(f"\n{q}: accuracy, mean (sd) over seeds, by budget as a share of visits")
            lines.append("policy".ljust(18) + "".join(f"{s:>14.2f}" for s in shares))
            for (policy,), prow in group(life_rows, "policy").items():
                cells = []
                for s in shares:
                    hit = [r for r in prow if round(r["budget"] / r["visits"], 2) == s]
                    m, sd = stat(hit, f"acc_{q}")
                    cells.append(f"{m:8.3f} ({sd:.3f})")
                lines.append(policy.ljust(18) + "".join(f"{c:>14}" for c in cells))
    return lines


def sweep_table(rows: list[dict], key: str, label: str) -> list[str]:
    lines = [f"\n## {label}"]
    for (budget_share,), brows in group(rows, "share_round").items():
        lines.append(f"\nbudget {budget_share:.2f} of visits; cells: all / changed accuracy")
        values = sorted({r["life"][key] for r in brows})
        lines.append("policy".ljust(18) + "".join(f"{v:>16}" for v in values))
        for (policy,), prow in group(brows, "policy").items():
            cells = []
            for v in values:
                hit = [r for r in prow if r["life"][key] == v]
                cells.append(f"{stat(hit, 'acc_all')[0]:.3f} / {stat(hit, 'acc_changed')[0]:.3f}")
            lines.append(policy.ljust(18) + "".join(f"{c:>16}" for c in cells))
        novelty = [stat([r for r in brows if r["life"][key] == v], "novelty_share")[0] for v in values]
        lines.append("novelty share".ljust(18) + "".join(f"{n:>16.3f}" for n in novelty))
    return lines


def lookalike_table(rows: list[dict], key: str, label: str) -> list[str]:
    """Rows: policy; columns: values of ``key``; cells: all / rare / changed accuracy."""
    lines = [f"\n## {label}"]
    for (life, share), lrows in sorted(group(rows, "name", "share_round").items()):
        values = sorted({r["life"][key] for r in lrows})
        lines.append(f"\n{life}, budget {share:.2f} of visits; cells: all / rare / changed")
        lines.append("policy".ljust(20) + "".join(f"{v:>22}" for v in values))
        for (policy,), prow in group(lrows, "policy").items():
            cells = []
            for v in values:
                hit = [r for r in prow if r["life"][key] == v]
                cells.append(" / ".join(f"{stat(hit, f'acc_{q}')[0]:.3f}" for q in ("all", "rare", "changed")))
            lines.append(policy.ljust(20) + "".join(f"{c:>22}" for c in cells))
    return lines


def growth_table(rows: list[dict]) -> list[str]:
    lines = ["\n## Novelty as a life gets longer (no memory involved)"]
    for (life,), lrows in group(rows, "name").items():
        days = sorted({r["life"]["days"] for r in lrows})
        visits = [stat([r for r in lrows if r["life"]["days"] == d], "visits")[0] for d in days]
        places = [stat([r for r in lrows if r["life"]["days"] == d], "places")[0] for d in days]
        novelty = [stat([r for r in lrows if r["life"]["days"] == d], "novelty")[0] for d in days]
        slope = np.polyfit(np.log(visits), np.log(novelty), 1)[0]
        place_slope = np.polyfit(np.log(visits), np.log(places), 1)[0]
        lines.append(f"\n{life}: novelty ~ visits^{slope:.2f}, places ~ visits^{place_slope:.2f}")
        lines.append("  days".ljust(10) + "".join(f"{d:>9}" for d in days))
        lines.append("  visits".ljust(10) + "".join(f"{v:>9.0f}" for v in visits))
        lines.append("  places".ljust(10) + "".join(f"{v:>9.0f}" for v in places))
        lines.append("  novelty".ljust(10) + "".join(f"{v:>9.0f}" for v in novelty))
        lines.append("  share".ljust(10) + "".join(f"{n / v:>9.3f}" for n, v in zip(novelty, visits, strict=True)))
    return lines


def nuisance_table(rows: list[dict]) -> list[str]:
    lines = ["\n## Nuisance (office, budget 0.20 of visits): changed accuracy, light x passer-by"]
    cells = sorted({(r["life"]["light"], r["life"]["passerby"]) for r in rows})
    lines.append("policy".ljust(18) + "".join(f"{f'{a:g}/{p:g}':>10}" for a, p in cells))
    for (policy,), prow in group(rows, "policy").items():
        out = []
        for a, p in cells:
            hit = [r for r in prow if r["life"]["light"] == a and r["life"]["passerby"] == p]
            out.append(f"{stat(hit, 'acc_changed')[0]:.3f}")
        lines.append(policy.ljust(18) + "".join(f"{c:>10}" for c in out))
    return lines


def years_table(rows: list[dict]) -> list[str]:
    lines = ["\n## Longer lives at a fixed number of stored observations: all-query accuracy"]
    for (life,), lrows in group(rows, "name").items():
        days = sorted({r["life"]["days"] for r in lrows})
        budgets = sorted({r["budget"] for r in lrows})
        lines.append(f"\n{life}")
        for (policy,), prow in group(lrows, "policy").items():
            lines.append(f"  {policy}")
            lines.append("    items".ljust(12) + "".join(f"{d:>8}d" for d in days))
            for b in budgets:
                vals = [
                    stat([r for r in prow if r["budget"] == b and r["life"]["days"] == d], "acc_all")[0] for d in days
                ]
                lines.append(f"    {b:<8}" + "".join(f"{v:>9.3f}" for v in vals))
        for d in days:
            drow = [r for r in lrows if r["life"]["days"] == d and r["policy"] == "oracle"]
            lines.append(
                f"  {d} days: {stat(drow, 'visits')[0]:.0f} visits, {stat(drow, 'places')[0]:.0f} places, "
                f"novelty {stat(drow, 'novelty')[0]:.0f}"
            )
    return lines


def figure(rows: list[dict], path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    shown = ["fifo", "reservoir", "memory_novelty", "place_cover_random", "place_nearest", "place_predict", "oracle"]
    lives = ["office", "student", "average", "nomad"]
    fig, axes = plt.subplots(2, 4, figsize=(16, 7), sharex=True, sharey=True)
    for col, life in enumerate(lives):
        lrows = [r for r in rows if r["life"]["name"] == life]
        share = stat([r for r in lrows if r["policy"] == "oracle"], "novelty_share")[0]
        for row, q in enumerate(("all", "changed")):
            ax = axes[row, col]
            for policy in shown:
                prow = group([r for r in lrows if r["policy"] == policy], "share_round")
                xs = sorted(prow)
                ax.errorbar(
                    [x[0] for x in xs],
                    [stat(prow[x], f"acc_{q}")[0] for x in xs],
                    yerr=[stat(prow[x], f"acc_{q}")[1] for x in xs],
                    label=policy,
                    marker="o",
                    ms=3,
                    capsize=2,
                )
            ax.axvline(share, color="k", ls=":", lw=1)
            ax.set_xscale("log")
            ax.set_title(f"{life}: {q} questions")
            if row == 1:
                ax.set_xlabel("stored observations / visits")
            if col == 0:
                ax.set_ylabel("accuracy")
    axes[0, 0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="eval/results/memory-sim.json")
    parser.add_argument("--figure", default="eval/results/memory-sim.png")
    parser.add_argument("--output", default="eval/results/memory-sim.txt")
    args = parser.parse_args()
    with open(args.input) as f:
        results = json.load(f)
    lines: list[str] = []
    for rows in results.values():
        for r in rows:
            if "budget" in r:
                r["share_round"] = round(r["budget"] / r["visits"], 2)
    if "main" in results:
        lines += main_table(results["main"])
        figure(results["main"], args.figure)
    if "change_rate" in results:
        lines += sweep_table(results["change_rate"], "changes_per_day", "Change rate sweep (average life)")
    if "nuisance" in results:
        lines += nuisance_table(results["nuisance"])
    if "transient_size" in results:
        lines += lookalike_table(results["transient_size"], "passerby_size", "Passer-by size (office)")
    if "lookalike" in results:
        lines += lookalike_table(results["lookalike"], "lookalike", "Share of lookalike places")
    if "years" in results:
        lines += years_table(results["years"])
    if "growth" in results:
        lines += growth_table(results["growth"])
    text = "\n".join(lines)
    with open(args.output, "w") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
