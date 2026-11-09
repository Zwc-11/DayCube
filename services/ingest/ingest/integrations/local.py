"""A language model served on this computer through an OpenAI-compatible chat API, such as
llama.cpp's ``llama-server`` (``llama-server -m qwen.gguf --port 8080``): nothing leaves the PC."""

from __future__ import annotations

import httpx


class LocalModel:
    def __init__(self, url: str, model: str, *, timeout_s: float = 180.0, transport: httpx.BaseTransport | None = None) -> None:
        """``url``: the API's base, ``http://127.0.0.1:8080/v1``. Only addresses on this computer
        are accepted, so a setting cannot send the day elsewhere by mistake."""
        host = httpx.URL(url).host
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError(f"DAYCUBE_LOCAL_LLM_URL must be on this computer (127.0.0.1), not {host!r}")
        self.url, self.model = url.rstrip("/"), model
        self.client = httpx.Client(timeout=timeout_s, transport=transport, trust_env=False)

    @property
    def name(self) -> str:
        return f"local {self.model}"

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        response = self.client.post(f"{self.url}/chat/completions", json={
            "model": self.model, "max_tokens": max_tokens, "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })  # fmt: skip
        response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            raise ValueError("the local model returned no answer")
        return str((choices[0].get("message") or {}).get("content") or "")

    def close(self) -> None:
        self.client.close()
