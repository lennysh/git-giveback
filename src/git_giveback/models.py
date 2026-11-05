"""Data types shared by providers and output formatters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

# Contribution kinds, in the order they are reported.
KINDS = ("pull_request", "review", "issue", "commits")


class ProviderError(Exception):
    """A provider could not complete a request (bad token, unknown user, API error...)."""


@dataclass
class Contribution:
    kind: str  # one of KINDS
    repo: str  # "owner/name" or "group/subgroup/project"
    title: str
    url: str
    date: datetime | None
    state: str = ""  # merged / open / draft / closed / approved / commented ...
    role: str = ""  # authored / assignee / reviewer
    ref: str = ""  # "owner/repo#12" or "group/project!12"
    count: int = 1  # number of commits (commits) or review submissions (review)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["date"] = self.date.isoformat() if self.date else None
        return data


@dataclass
class AccountResult:
    provider: str  # "github" | "gitlab"
    host: str
    user: str
    contributions: list[Contribution] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_note(self, note: str) -> None:
        if note not in self.notes:
            self.notes.append(note)
