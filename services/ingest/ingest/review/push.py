"""Yesterday's review on the phone in the morning: off unless ``DAYCUBE_SUMMARY_PUSH`` is set.

- ``ntfy``: a POST of the text to ``DAYCUBE_NTFY_URL`` (``https://ntfy.sh/<topic>`` or a server of
  your own). A topic on ntfy.sh can be read by anyone who knows its name: pick a long random one.
- ``telegram``: ``sendMessage`` with ``DAYCUBE_TELEGRAM_TOKEN`` (from @BotFather) to
  ``DAYCUBE_TELEGRAM_CHAT``.

The capture receiver's librarian sends it once per day, in its first round after
``DAYCUBE_PUSH_AT`` (08:00), when the review day before has a final review. A failed send is
tried again in the next rounds, at most ``ATTEMPTS`` times. ``vault/logs/push.json`` remembers what
was sent. What leaves the PC: the headline, up to three sentences, the first change and the first
two items for tomorrow.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

from ..vault import Record, Vault, read_text
from .summary import read_review

ATTEMPTS = 5
STATE = "logs/push.json"
TIMEOUT_S = 10.0


def message(review: Record) -> tuple[str, str]:
    """The push's title and text."""
    title = f"DayCube · {review['date']}"
    lines = [review["headline"]]
    lines += [s["text"] for s in review["sentences"][:3]]
    if review["improvements"]:
        lines.append("To change: " + review["improvements"][0]["text"])
    if review["tomorrow"]:
        lines.append("Tomorrow: " + "; ".join(t["text"] for t in review["tomorrow"][:2]))
    return title, "\n".join(lines)


def _open(request: Request) -> bytes:
    with build_opener(ProxyHandler()).open(request, timeout=TIMEOUT_S) as response:
        return response.read()


def send(settings: Any, title: str, text: str, opener: Callable[[Request], bytes] = _open) -> str:
    """Send by the configured channel; returns its name. Raises on a missing setting or a failure."""
    kind = (getattr(settings, "summary_push", "") or "").strip().lower()
    if kind == "ntfy":
        url = getattr(settings, "ntfy_url", "")
        if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            raise ValueError("DAYCUBE_NTFY_URL: an https address of a topic")
        opener(Request(url, data=text.encode("utf-8"), method="POST",
                       headers={"Title": title.encode("ascii", "replace").decode(), "Tags": "calendar"}))  # fmt: skip
        return "ntfy"
    if kind == "telegram":
        token = settings.telegram_token.get_secret_value() if hasattr(settings.telegram_token, "get_secret_value") else ""
        chat = getattr(settings, "telegram_chat", "")
        if not token or not chat:
            raise ValueError("DAYCUBE_TELEGRAM_TOKEN and DAYCUBE_TELEGRAM_CHAT are needed")
        body = json.dumps({"chat_id": chat, "text": f"{title}\n{text}", "disable_web_page_preview": True}).encode()
        answer = opener(Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST",
                                headers={"Content-Type": "application/json"}))  # fmt: skip
        if not json.loads(answer or b"{}").get("ok"):
            raise RuntimeError("Telegram did not accept the message")
        return "telegram"
    raise ValueError(f"DAYCUBE_SUMMARY_PUSH={kind!r}: ntfy or telegram")


def _state(vault: Vault) -> dict:
    path = vault.root / STATE
    try:
        return json.loads(read_text(path)) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def step(vault: Vault, settings: Any, now: datetime, zone: str, opener: Callable[[Request], bytes] = _open) -> Record | None:
    """Send yesterday's review if it is due; returns what happened, or None when nothing was due."""
    if not (getattr(settings, "summary_push", "") or "").strip():
        return None
    tz = ZoneInfo(zone)
    local = now.astimezone(tz)
    try:
        at = time.fromisoformat(getattr(settings, "push_at", "08:00") or "08:00")
    except ValueError:
        at = time(8, 0)
    if local.time() < at:
        return None
    day = (local.date() - timedelta(days=1) if local.time() >= time(4, 0) else local.date() - timedelta(days=2))
    key = day.isoformat()
    state = _state(vault)
    entry = state.get(key, {})
    if entry.get("sent") or entry.get("attempts", 0) >= ATTEMPTS:
        return None
    review = read_review(vault, key)
    if review is None or review["kind"] != "final":
        return None
    title, text = message(review)
    try:
        channel = send(settings, title, text, opener)
        entry = {"sent": local.isoformat(timespec="seconds"), "channel": channel, "attempts": entry.get("attempts", 0) + 1}
    except Exception as error:  # noqa: BLE001 - reported, tried again next round
        entry = {"attempts": entry.get("attempts", 0) + 1, "error": f"{type(error).__name__}: {error}"[:200]}
    keep = {k: v for k, v in state.items() if k >= (date.fromisoformat(key) - timedelta(days=30)).isoformat()}
    keep[key] = entry
    vault.write_json(vault.root / STATE, keep)
    return {"date": key, **entry}
