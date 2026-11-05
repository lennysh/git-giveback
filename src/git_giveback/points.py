"""Optional points scoring, using the same points.toml format as git-giveback-dashboard.

Rules are checked in order and the first match wins. Contributions that match
no rule are listed but score 0. Without ``--points``, the built-in rules below
are used. They mirror the original Red Hat CoP "Giveback Ninja" program.

Points here are self-reported estimates. A dashboard re-verifies every
contribution against GitHub before scoring it.
"""

from __future__ import annotations

import tomllib
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .scopes import KINDS

DEFAULT_POINTS_TOML = """
[[rule]]
label = "Merged pull requests"
kind = "pull_request"
state = "merged"
points = 1

[[rule]]
label = "Pull requests reviewed"
kind = "review"
points = 1

[[rule]]
label = "Issues closed as assignee"
kind = "issue"
role = "assignee"
points = 1
"""

RULE_KEYS = {"kind", "points", "state", "role", "scope", "per_commit", "label"}


class PointsError(Exception):
    pass


@dataclass
class Rule:
    kind: str
    points: float
    state: str | None = None
    role: str | None = None
    scope: str | None = None
    per_commit: bool = False
    label: str = ""

    def matches(self, kind: str, state: str, role: str, scope: str | None) -> bool:
        return (kind == self.kind
                and (self.state is None or state == self.state)
                and (self.role is None or role == self.role)
                and (self.scope is None or scope == self.scope))

    @property
    def description(self) -> str:
        if self.label:
            return self.label
        extra = ", ".join(f"{k}={v}" for k, v in (("state", self.state), ("role", self.role),
                                                   ("scope", self.scope)) if v)
        return f"{self.kind}{f' ({extra})' if extra else ''}{' per commit' if self.per_commit else ''}"


@dataclass
class Points:
    rules: list[Rule] = field(default_factory=list)
    since: date | None = None
    until: date | None = None

    def in_season(self, day: date | None) -> bool:
        if day is None:
            return self.since is None and self.until is None
        return (self.since is None or day >= self.since) and (self.until is None or day <= self.until)

    def match(self, kind: str, state: str, role: str, scope: str | None, day: date | None) -> Rule | None:
        if not self.in_season(day):
            return None
        return next((r for r in self.rules if r.matches(kind, state, role, scope)), None)


def _date(value, key: str) -> date | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        raise PointsError(f"points: {key} must be YYYY-MM-DD") from None


def parse_points(data: dict) -> Points:
    rules = []
    for index, entry in enumerate(data.get("rule", []), 1):
        unknown = set(entry) - RULE_KEYS
        if unknown:
            raise PointsError(f"points rule #{index}: unknown keys {sorted(unknown)}")
        if entry.get("kind") not in KINDS or not isinstance(entry.get("points"), (int, float)) \
                or isinstance(entry.get("points"), bool):
            raise PointsError(f"points rule #{index}: needs a valid kind and numeric points")
        rules.append(Rule(entry["kind"], float(entry["points"]), entry.get("state"), entry.get("role"),
                          entry.get("scope"), bool(entry.get("per_commit", False)), entry.get("label", "")))
    return Points(rules, _date(data.get("since"), "since"), _date(data.get("until"), "until"))


def default_points() -> Points:
    return parse_points(tomllib.loads(DEFAULT_POINTS_TOML))


def load_points(source: str) -> Points:
    """Load rules from a local path or an http(s) URL."""
    try:
        if source.startswith(("https://", "http://")):
            with urllib.request.urlopen(source, timeout=30) as resp:
                text = resp.read().decode("utf-8")
        else:
            text = Path(source).expanduser().read_text(encoding="utf-8")
        return parse_points(tomllib.loads(text))
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise PointsError(f"cannot load points from {source}: {err}") from None
