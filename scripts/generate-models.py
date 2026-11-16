"""Generate Pydantic models from the canonical JSON Schemas, never the reverse.

Python checks every contract against its JSON Schema (ingest/contracts.py). A Pydantic model is
generated only for a contract a route takes as its request body: add its name to MODELS."""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services/ingest/ingest/generated"
MODELS = ("event",)
for schema in (ROOT / "packages/schemas" / f"{name}.schema.json" for name in MODELS):
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "models.py"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(schema),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(output),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.11",
                "--disable-timestamp",
                "--use-standard-collections",
                "--use-schema-description",
                "--formatters",
                "black",
                "isort",
            ],
            check=True,
        )
        content = output.read_text(encoding="utf-8")
        target = TARGET / f"{schema.name.removesuffix('.schema.json')}.py"
        # Compare the same Ruff-formatted source that contributors commit, regardless of cwd.
        content = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--config",
                str(ROOT / "pyproject.toml"),
                "--stdin-filename",
                str(target),
                "-",
            ],
            input=content,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if "--check" in sys.argv:
            existing = target.read_text(encoding="utf-8").replace("\r\n", "\n") if target.exists() else ""
            if existing != content.replace("\r\n", "\n"):
                raise SystemExit(f"Stale generated model: {target}")
        else:
            target.write_text(content, encoding="utf-8", newline="\n")
print("Pydantic models match JSON Schema contracts")
