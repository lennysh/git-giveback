"""Repository scopes: limit a report to an allowlist of repositories.

A scopes file is TOML with one ``[[scope]]`` table per allowed area::

    [[scope]]
    name = "Red Hat CoP"
    host = "github.com"
    owner = "redhat-cop"          # org / user (GitHub) or top-level group (GitLab)
    repos = "^sap-.*$"            # optional: regex the repo name must match
    exclude = "^old-.*$"          # optional: regex of repo names to skip
    types = ["pull_request"]      # optional: contribution types that count

``repos`` and ``exclude`` are matched (case-insensitively) against the repo
path below the owner, e.g. ``infra.aap_configuration`` or, for GitLab
subgroups, ``subgroup/project``.

This module is copied verbatim into git-giveback-dashboard; keep them in sync.
"""

from __future__ import annotations

import re
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

KINDS = ("pull_request", "review", "issue", "commits")
ALLOWED_KEYS = {"name", "host", "owner", "repos", "exclude", "types"}


class ScopeError(Exception):
    pass


@dataclass
class Scope:
    name: str
    host: str
    owner: str
    repos: re.Pattern | None = None
    exclude: re.Pattern | None = None
    types: tuple[str, ...] | None = None

    def matches(self, host: str, repo: str, kind: str) -> bool:
        if host.lower() != self.host.lower():
            return False
        owner, _, name = repo.partition("/")
        if owner.lower() != self.owner.lower() or not name:
            return False
        if self.types is not None and kind not in self.types:
            return False
        if self.repos is not None and not self.repos.fullmatch(name):
            return False
        return not (self.exclude is not None and self.exclude.fullmatch(name))


def _regex(value, where: str) -> re.Pattern | None:
    if value is None:
        return None
    try:
        return re.compile(value, re.IGNORECASE)
    except (re.error, TypeError) as err:
        raise ScopeError(f"{where}: invalid regex {value!r}: {err}") from None


def parse_scopes(data: dict) -> list[Scope]:
    entries = data.get("scope")
    if not isinstance(entries, list) or not entries:
        raise ScopeError("scopes file must contain at least one [[scope]] table")
    scopes = []
    for index, entry in enumerate(entries, 1):
        where = f"scope #{index}"
        unknown = set(entry) - ALLOWED_KEYS
        if unknown:
            raise ScopeError(f"{where}: unknown keys {sorted(unknown)}")
        missing = [k for k in ("name", "host", "owner") if not entry.get(k)]
        if missing:
            raise ScopeError(f"{where}: missing {', '.join(missing)}")
        types = entry.get("types")
        if types is not None:
            bad = [t for t in types if t not in KINDS]
            if bad or not types:
                raise ScopeError(f"{where}: types must be a non-empty subset of {list(KINDS)}")
            types = tuple(types)
        scopes.append(Scope(
            name=entry["name"], host=entry["host"], owner=entry["owner"],
            repos=_regex(entry.get("repos"), where), exclude=_regex(entry.get("exclude"), where), types=types,
        ))
    return scopes


def load_scopes(source: str) -> list[Scope]:
    """Load scopes from a local path or an http(s) URL."""
    try:
        if source.startswith(("https://", "http://")):
            with urllib.request.urlopen(source, timeout=30) as resp:
                text = resp.read().decode("utf-8")
        else:
            text = Path(source).expanduser().read_text(encoding="utf-8")
        return parse_scopes(tomllib.loads(text))
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise ScopeError(f"cannot load scopes from {source}: {err}") from None


def scope_for(scopes: list[Scope], host: str, repo: str, kind: str) -> Scope | None:
    return next((s for s in scopes if s.matches(host, repo, kind)), None)
