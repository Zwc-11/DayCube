import json
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .config import PROJECT_ROOT


@lru_cache
def validator(name: str) -> Draft202012Validator:
    path = PROJECT_ROOT / "packages/schemas" / f"{name}.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(name: str, value: Any) -> None:
    validator(name).validate(value)
