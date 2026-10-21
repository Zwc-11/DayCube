"""Generate Pydantic models from the canonical JSON Schemas, never the reverse."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services/ingest/ingest/generated"
TARGET.mkdir(parents=True, exist_ok=True)
for schema in sorted((ROOT / "packages/schemas").glob("*.schema.json")):
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "models.py"
        subprocess.run([
            sys.executable, "-m", "datamodel_code_generator", "--input", str(schema),
            "--input-file-type", "jsonschema", "--output", str(output),
            "--output-model-type", "pydantic_v2.BaseModel", "--target-python-version", "3.11",
            "--disable-timestamp", "--use-standard-collections", "--use-schema-description",
            "--formatters", "black", "isort",
        ], check=True)
        content = output.read_text(encoding="utf-8")
        target = TARGET / f"{schema.name.removesuffix('.schema.json')}.py"
        if "--check" in sys.argv:
            existing = target.read_text(encoding="utf-8").replace("\r\n", "\n") if target.exists() else ""
            if existing != content.replace("\r\n", "\n"):
                raise SystemExit(f"Stale generated model: {target}")
        else:
            target.write_text(content, encoding="utf-8")
print("Pydantic models match JSON Schema contracts")
