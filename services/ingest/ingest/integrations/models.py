"""Which language model a use of one is given, from its setting: ``""`` (none: everything stays on
this computer and code alone does the work), ``anthropic`` or ``local``."""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..config import Settings


class LanguageModel(Protocol):
    name: str

    def complete(self, system: str, user: str, max_tokens: int) -> str: ...


def json_answer(text: str) -> dict[str, Any]:
    """The JSON object in a model's answer (models wrap it in prose or a code fence), or ValueError."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the model's answer has no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("the model's answer is not a JSON object")
    return value


def language_model(choice: str, setting: str) -> tuple[LanguageModel | None, str]:
    """The model for ``choice`` (the value of ``setting``, named in messages), or None and why not."""
    choice = choice.strip().casefold()
    if not choice:
        return None, f"off ({setting} is not set)"
    if choice == "anthropic":
        from .anthropic import AnthropicAdapter

        try:
            model: LanguageModel = AnthropicAdapter()
        except RuntimeError as error:
            return None, f"off: {error}"
        return model, model.name
    if choice == "local":
        from .local import LocalModel

        settings = Settings()
        try:
            model = LocalModel(settings.local_llm_url, settings.local_llm_model)
        except RuntimeError as error:
            return None, f"off: {error}"
        return model, model.name
    return None, f"off: {setting}={choice!r} is not a known model (anthropic, local)"
