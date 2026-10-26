"""Inventory tracked first-party files and preserve hash-bound manual review notes.

This checks syntax and records structural review signals; it does not certify algorithm quality
or performance. The ledger distinguishes those checks from manually reviewed implementations.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {".py", ".ts", ".tsx", ".js", ".mjs", ".mts"}
ASSETS = {".png", ".gif", ".mp4", ".woff2", ".svg"}
BINARY = ASSETS | {
    ".zip",
    ".m4a",
    ".mp3",
    ".wav",
    ".webm",
    ".npz",
    ".npy",
    ".jpg",
    ".jpeg",
    ".glb",
    ".ply",
    ".splat",
    ".pdf",
    ".zst",
    ".gz",
}


def category(path: str) -> str:
    name = Path(path)
    if "/generated/" in path or path in {"packages/schemas/src/types.ts", "packages/schemas/src/contracts.json"}:
        return "generated"
    if name.suffix == ".lock" or name.name == "package-lock.json":
        return "lockfile"
    if name.suffix in ASSETS or "/assets/" in path:
        return "asset"
    if path.startswith(("fixtures/", "eval/fixtures/")) or path == "apps/viewer/src/data/sampleDay.ts":
        return "fixture"
    if path.startswith("eval/results/") or name.suffix == ".jsonl":
        return "measurement_or_fixture"
    if name.suffix == ".md" or name.suffix == ".txt":
        return "documentation"
    if name.suffix in SOURCE:
        if "/tests/" in path or "/e2e/" in path or "/browser-tests/" in path or ".test." in path:
            return "test"
        return "source"
    return "configuration"


def python_signals(text: str, path: str) -> tuple[list[str], list[str]]:
    tree = ast.parse(text, filename=path)
    functions, signals = [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            loops = sum(isinstance(n, (ast.For, ast.While, ast.AsyncFor, ast.comprehension)) for n in ast.walk(node))
            functions.append(f"{node.name}:{node.lineno} (loops including nested scopes: {loops})")
        if isinstance(node, ast.Call):
            target = ast.unparse(node.func)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"read_text", "write_text", "open"}
                and not any(k.arg == "encoding" for k in node.keywords)
            ):
                signals.append(f"{node.lineno}: check text/binary mode and default encoding at {target}")
            if target.endswith(("subprocess.run", "subprocess.check_output", "subprocess.Popen")):
                signals.append(f"{node.lineno}: check subprocess timeout or explicit process ownership at {target}")
    return functions, signals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/review-ledger.json")
    parser.add_argument("--tracked-only", action="store_true", help="Exclude unrelated untracked drafts")
    args = parser.parse_args()
    target = args.output.resolve()
    previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    notes = {entry["path"]: entry for entry in previous.get("files", [])}
    command = ["git", "ls-files", "--cached", "-z"]
    if not args.tracked_only:
        command.extend(["--others", "--exclude-standard"])
    raw = subprocess.check_output(command, cwd=ROOT)
    paths = sorted(set(raw.decode("utf-8").strip("\0").split("\0")))
    files, scripts = [], []
    for path in paths:
        source = ROOT / path
        if source.resolve() == target or not source.is_file():
            continue
        data = source.read_bytes()
        kind = category(path)
        entry: dict = {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "category": kind, "bytes": len(data)}
        if kind != "asset" and source.suffix not in BINARY:
            text = data.decode("utf-8")
            entry["lines"] = len(text.splitlines())
            if source.suffix == ".py":
                entry["functions"], entry["review_signals"] = python_signals(text, path)
                entry["structural_check"] = "Python AST parsed; Ruff and suite results recorded in the review report"
            elif source.suffix in {".json", ".jsonl"}:
                for item in text.splitlines() if source.suffix == ".jsonl" else [text]:
                    if item.strip():
                        json.loads(item)
                entry["structural_check"] = "JSON parsed"
            elif source.suffix in {".toml", ".lock"}:
                tomllib.loads(text)
                entry["structural_check"] = "TOML parsed"
            elif source.suffix in SOURCE:
                scripts.append(path)
                entry["structural_check"] = (
                    "TypeScript parser (JS/TS/TSX); project typechecks and tests recorded separately"
                )
            else:
                entry["structural_check"] = "UTF-8 text inventory; build/configuration review is separate"
        else:
            entry["structural_check"] = (
                "Binary fixture/media or asset hash and size; producer/consumer review is separate"
            )
        old = notes.get(path, {})
        if old.get("sha256") == entry["sha256"] and old.get("manual_review"):
            entry["manual_review"] = old["manual_review"]
        elif old.get("manual_review"):
            entry["previous_review_needs_refresh"] = old["manual_review"]
        elif old.get("previous_review_needs_refresh"):
            entry["previous_review_needs_refresh"] = old["previous_review_needs_refresh"]
        files.append(entry)
    script = """
import ts from 'typescript';
import fs from 'node:fs';
const paths = JSON.parse(fs.readFileSync(0, 'utf8'));
const errors = [];
for (const path of paths) {
  const source = ts.createSourceFile(path, fs.readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true);
  for (const diagnostic of source.parseDiagnostics) {
    errors.push({path, message: ts.flattenDiagnosticMessageText(diagnostic.messageText, '\\n')});
  }
}
process.stdout.write(JSON.stringify(errors));
"""
    run = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(scripts),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    errors = json.loads(run.stdout)
    if errors:
        raise SystemExit(json.dumps(errors, indent=2))
    report = {
        "baseline_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "scope": ("Every tracked file" if args.tracked_only else "Every tracked file and new non-ignored file")
        + ", except this generated ledger itself. No vendored source is tracked; dependencies live in ignored environments and lockfiles.",
        "limits": "Structural checks cover every listed file; manual_review only identifies implementations actually examined. Neither is a proof of maximum efficiency, a dependency audit or DeepInfer throughput parity. Missing manual_review is explicitly unreviewed at the semantic level.",
        "counts": dict(Counter(entry["category"] for entry in files)),
        "manual_reviews": sum("manual_review" in entry for entry in files),
        "files": files,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"files": len(files), "counts": report["counts"], "manual_reviews": report["manual_reviews"]}))


if __name__ == "__main__":
    main()
