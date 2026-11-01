"""Commits made during a review day in your projects' repositories: what got finished at the desk.

The repositories are the folders of ``vault/projects.yaml`` that are git repositories (each named
by its project), and the folder a vault named ``vault`` is in when that is one and no project lists
it (the DayCube checkout itself). Every author's commits are read; the repository's own
``user.email`` (or the global one) tells yours from the others' (an agent committing for you, a
teammate), which the review lists by author name. With no email set, all count as yours. All
branches are read and merges are left out. ``git`` runs read-only, with
``GIT_OPTIONAL_LOCKS=0`` so it never takes the index lock of a checkout you are working in, and
without a console window on Windows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath

import yaml

from ..time import instant
from ..vault import Vault, read_text
from .labels import PROJECTS_FILE

TIMEOUT_S = 15.0
SEP = "\x1f"
SLACK = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Commit:
    project: str
    sha: str
    t: str  # author date, ISO 8601
    subject: str
    author: str = ""  # the author's name
    mine: bool = True  # authored with your user.email

    @property
    def id(self) -> str:
        return f"git:{self.project}:{self.sha[:10]}"


def _folder(value: str) -> Path:
    """A folder as written in projects.yaml ('E:\\DAYCUBE' or a POSIX path), for this system."""
    return Path(value) if os.name == "nt" or not PureWindowsPath(value).drive else Path(PureWindowsPath(value))


def _is_repo(folder: Path) -> bool:
    return (folder / ".git").exists()


def repositories(vault: Vault) -> list[tuple[str, Path]]:
    """(project, folder) for each repository to read, the projects' order kept."""
    out: list[tuple[str, Path]] = []
    path = vault.root / PROJECTS_FILE
    if path.exists():
        try:
            data = yaml.safe_load(read_text(path)) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        projects = data.get("projects") if isinstance(data, dict) else None
        for name, item in (projects or {}).items() if isinstance(projects, dict) else []:
            folders = (item or {}).get("folders") if isinstance(item, dict) else None
            for value in folders if isinstance(folders, list) else [folders] if isinstance(folders, str) else []:
                folder = _folder(str(value).strip())
                if str(value).strip() and _is_repo(folder):
                    out.append((str(name), folder))
    home = vault.root.resolve().parent  # E:\DAYCUBE for E:\DAYCUBE\vault: the repository DayCube is
    if vault.root.name == "vault" and _is_repo(home) and not any(_same(home, folder) for _, folder in out):
        out.append((home.name, home))
    seen: set[str] = set()
    unique = []
    for name, folder in out:
        key = os.path.normcase(str(folder.resolve()))
        if key not in seen:
            seen.add(key)
            unique.append((name, folder))
    return unique


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return False


def _git(folder: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(folder), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=TIMEOUT_S, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
    )  # fmt: skip


def commits(vault: Vault, start: datetime, stop: datetime) -> tuple[list[Commit], list[str]]:
    """The commits authored in [start, stop), oldest first (``mine`` tells yours), and what could
    not be read."""
    found: dict[str, Commit] = {}
    errors: list[str] = []
    for project, folder in repositories(vault):
        try:
            email = _git(folder, "config", "user.email").stdout.strip()
            # git's --since is the committer date and stops walking at the first older commit, so it
            # only narrows the walk (a day early); the author date picks the day's commits below.
            run = _git(folder, "log", "--all", "--no-merges", f"--since={(start - SLACK).isoformat()}",
                       f"--format=%H{SEP}%aI{SEP}%ae{SEP}%an{SEP}%s")  # fmt: skip
        except FileNotFoundError:
            return [], ["git is not installed: commits are not counted"]
        except (subprocess.TimeoutExpired, OSError) as error:
            errors.append(f"{project}: git did not answer ({type(error).__name__})")
            continue
        if run.returncode != 0:
            errors.append(f"{project}: git log failed: {run.stderr.strip()[:200]}")
            continue
        for line in run.stdout.splitlines():
            parts = line.split(SEP)
            if len(parts) != 5:
                continue
            sha, when, address, name, subject = parts
            try:
                moment = instant(when)
            except ValueError:
                continue
            if start <= moment < stop and sha not in found:
                mine = not email or address.casefold() == email.casefold()
                found[sha] = Commit(project, sha, moment.isoformat(), " ".join(subject.split()), name.strip(), mine)
    return sorted(found.values(), key=lambda c: c.t), errors
