"""Things finished on websites, read from the page text the extension already sends (``blocks.jsonl``).

A rule names a site, the pages that count (a pattern on the address) and the words on such a page
that say it was finished. LeetCode's accepted submissions are built in; more go in
``vault/sites.yaml``::

    sites:
      - name: LeetCode            # built in: shown here as an example of the form
        domain: leetcode.com      # a page's address must contain it (it also keeps the scan fast)
        path: /problems/([^/]+)/submissions/   # a pattern on the address; its group names the item
        text: \\bAccepted\\b        # a pattern on the page's text
        label: accepted
    defaults: true                # false: only the sites above

Each page counts once a day, at the first time it showed the words. Only pages the extension
recorded are seen: excluded sites and private windows are not.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

import yaml

from ..time import instant
from ..vault import Record, Vault, read_text, stored, text_lines

FILE = "sites.yaml"


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    domain: str
    path: re.Pattern[str]
    text: re.Pattern[str]
    label: str


BUILT_IN = (
    Rule("LeetCode", "leetcode.com", re.compile(r"/problems/([^/?#]+)/submissions/"), re.compile(r"\bAccepted\b"),
         "accepted"),
)  # fmt: skip


def rules(vault: Vault) -> tuple[list[Rule], list[str]]:
    """The rules in use, and what in ``sites.yaml`` was left out."""
    path = vault.root / FILE
    found: list[Rule] = []
    errors: list[str] = []
    defaults = True
    if path.exists():
        try:
            data = yaml.safe_load(read_text(path)) or {}
            defaults = bool(data.get("defaults", True)) if isinstance(data, dict) else True
            for n, item in enumerate((data.get("sites") or []) if isinstance(data, dict) else []):
                try:
                    found.append(Rule(str(item["name"]), str(item["domain"]).lower(), re.compile(str(item["path"])),
                                      re.compile(str(item["text"])), str(item.get("label") or "done")))  # fmt: skip
                except (KeyError, TypeError, re.error) as error:
                    errors.append(f"{FILE} site {n + 1} left out: {type(error).__name__}")
        except (OSError, yaml.YAMLError) as error:
            errors.append(f"{FILE} left out: {type(error).__name__}")
    names = {r.name.casefold() for r in found}
    if defaults:
        found += [r for r in BUILT_IN if r.name.casefold() not in names]
    return found, errors


@dataclass(frozen=True, slots=True)
class Hit:
    t: str
    rule: Rule
    item: str  # what was finished: the pattern's group, or the page's title
    url: str
    title: str

    @property
    def id(self) -> str:
        return "site:" + hashlib.blake2b(f"{self.rule.name}|{self.url}".encode(), digest_size=6).hexdigest()


def finished(vault: Vault, days: list[str], start: datetime, stop: datetime, found: list[Rule]) -> list[Hit]:
    """The pages that showed a rule's words inside the window, once each, oldest first."""
    if not found:
        return []
    domains = tuple({r.domain for r in found})
    hits: dict[tuple[str, str], Hit] = {}
    for day in days:
        path = vault.root / "capture" / day / "blocks.jsonl"
        if not stored(path).exists():
            continue
        for line in text_lines(path):
            if not any(d in line for d in domains):  # most of the file never reaches the parser
                continue
            try:
                record: Record = json.loads(line)
                moment = instant(record["t"])
            except (ValueError, KeyError, TypeError):
                continue
            if not start <= moment < stop:
                continue
            url = str(record.get("url") or "")
            text = " ".join(str(b.get("text") or "") for b in record.get("blocks") or [] if isinstance(b, dict))
            for rule in found:
                if rule.domain not in url.lower() or (rule.name, url) in hits:
                    continue
                page = rule.path.search(url)
                if page and rule.text.search(text):
                    item = page.group(1) if page.groups() and page.group(1) else str(record.get("title") or url)
                    hits[(rule.name, url)] = Hit(moment.isoformat(), rule, item.replace("-", " "), url,
                                                 str(record.get("title") or ""))  # fmt: skip
    return sorted(hits.values(), key=lambda h: h.t)
