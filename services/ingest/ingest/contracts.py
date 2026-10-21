"""JSON Schema contracts compiled once into native validators.

The schemas in ``packages/schemas`` stay canonical. Compiling them with ``jsonschema-rs``
keeps the same Draft 2020-12 semantics (including ``format``) at roughly 200x the speed of the
pure-Python validator, which was 96% of an index rebuild on a 5,000-event day.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cache
from typing import Any

import jsonschema_rs

from .config import PROJECT_ROOT

SCHEMA_DIR = PROJECT_ROOT / "packages/schemas"


class ContractError(ValueError):
    """A record does not match its schema. ``path`` is the JSON path of the offending value."""

    def __init__(self, contract: str, message: str, path: list[str | int]) -> None:
        super().__init__(f"{contract}: {message}")
        self.contract = contract
        self.message = message
        self.path = path


@cache
def validator(name: str) -> jsonschema_rs.Draft202012Validator:
    """Compile a schema once per process. Compilation also rejects an invalid schema."""
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    return jsonschema_rs.Draft202012Validator(schema, validate_formats=True)


def validate(name: str, value: Any) -> None:
    try:
        validator(name).validate(value)
    except jsonschema_rs.ValidationError as error:
        raise ContractError(name, error.message, list(error.instance_path)) from None


def validate_all(name: str, values: Iterable[Any]) -> None:
    """Check many records; the boolean fast path defers error formatting to the first failure."""
    check = validator(name)
    for value in values:
        if not check.is_valid(value):
            validate(name, value)
