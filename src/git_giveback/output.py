"""Report renderers: table (terminal), markdown, json and csv."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, tzinfo

from .dates import format_range
from .models import KINDS, AccountResult, Contribution

FORMATS = ("table", "markdown", "json", "csv")
PR_STATE_ORDER = ("merged", "open", "draft", "closed", "locked")


@dataclass
class Report:
    since: datetime
    until: datetime
    tz: tzinfo
    accounts: list[AccountResult]


def kind_title(kind: str, provider: str) -> str:
    return {
        "pull_request": "Merge requests" if provider == "gitlab" else "Pull requests",
        "review": "Reviews",
        "issue": "Issues",
        "commits": "Pushed commits" if provider == "gitlab" else "Commits",
    }[kind]


def grouped(contributions: list[Contribution]) -> dict[str, list[Contribution]]:
    groups: dict[str, list[Contribution]] = {kind: [] for kind in KINDS}
    for item in contributions:
        groups.setdefault(item.kind, []).append(item)
    for items in groups.values():
        items.sort(key=lambda c: (c.date is not None, c.date), reverse=True)
    return groups


def summary(account: AccountResult) -> str:
    groups = grouped(account.contributions)
    parts = []
    prs = groups["pull_request"]
    if prs:
        states = Counter(c.state for c in prs)
        ordered = [s for s in PR_STATE_ORDER if s in states] + sorted(set(states) - set(PR_STATE_ORDER))
        detail = ", ".join(f"{states[s]} {s}" for s in ordered)
        parts.append(f"{len(prs)} {kind_title('pull_request', account.provider).lower()} ({detail})")
    if groups["review"]:
        parts.append(f"{len(groups['review'])} reviewed")
    if groups["issue"]:
        parts.append(f"{len(groups['issue'])} issues")
    if groups["commits"]:
        total = sum(c.count for c in groups["commits"])
        noun = "pushed commits" if account.provider == "gitlab" else "commits"
        parts.append(f"{total} {noun} in {len(groups['commits'])} repos")
    return " · ".join(parts) or "no contributions found"


def _date(item: Contribution, tz: tzinfo) -> str:
    return item.date.astimezone(tz).strftime("%Y-%m-%d") if item.date else "----------"


def _label(item: Contribution) -> str:
    if item.kind == "commits":
        return f"{item.count} commit{'s' if item.count != 1 else ''}"
    if item.kind == "issue" and "assignee" in item.role:
        return f"{item.state} (assignee)"
    return item.state


def render_table(report: Report) -> str:
    lines = [f"Git contributions {format_range(report.since, report.until, report.tz)}", ""]
    for account in report.accounts:
        lines.append(f"{account.host} · @{account.user}")
        lines.append(f"  {summary(account)}")
        for kind, items in grouped(account.contributions).items():
            if not items:
                continue
            lines += ["", f"  {kind_title(kind, account.provider)} ({len(items)})"]
            label_w = max(len(_label(c)) for c in items)
            ref_w = max(len(c.ref) for c in items)
            for c in items:
                row = f"    {_date(c, report.tz)}  {_label(c):<{label_w}}  {c.ref:<{ref_w}}  {c.title}"
                lines.append(row.rstrip())
        lines += _notes(account, "  ")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _notes(account: AccountResult, indent: str) -> list[str]:
    out = []
    if account.errors or account.notes:
        out.append("")
    out += [f"{indent}ERROR: {e}" for e in account.errors]
    out += [f"{indent}note: {n}" for n in account.notes]
    return out


_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|])")


def md_escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def render_markdown(report: Report) -> str:
    lines = [f"# Git contributions: {format_range(report.since, report.until, report.tz)}", ""]
    for account in report.accounts:
        lines += [f"## {account.host} — @{account.user}", "", f"**Summary:** {summary(account)}", ""]
        for kind, items in grouped(account.contributions).items():
            if not items:
                continue
            lines += [f"### {kind_title(kind, account.provider)} ({len(items)})", ""]
            for c in items:
                if kind == "commits":
                    lines.append(f"- [{md_escape(c.ref)}]({c.url}) — {_label(c)} (latest {_date(c, report.tz)})")
                else:
                    lines.append(f"- {_date(c, report.tz)} · **{_label(c)}** · "
                                 f"[{md_escape(c.ref)}]({c.url}) — {md_escape(c.title)}")
            lines.append("")
        if account.errors or account.notes:
            lines += [f"> **Error:** {md_escape(e)}" for e in account.errors]
            lines += [f"> Note: {md_escape(n)}" for n in account.notes]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json(report: Report) -> str:
    data = {
        "since": report.since.isoformat(),
        "until": report.until.isoformat(),
        "accounts": [
            {
                "provider": a.provider, "host": a.host, "user": a.user, "summary": summary(a),
                "contributions": [c.to_dict() for c in a.contributions],
                "notes": a.notes, "errors": a.errors,
            }
            for a in report.accounts
        ],
    }
    return json.dumps(data, indent=2) + "\n"


CSV_FIELDS = ("provider", "host", "user", "kind", "repo", "ref", "title", "url", "date", "state", "role", "count")


def render_csv(report: Report) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_FIELDS)
    for account in report.accounts:
        for c in account.contributions:
            writer.writerow([account.provider, account.host, account.user, c.kind, c.repo, c.ref, c.title,
                             c.url, c.date.isoformat() if c.date else "", c.state, c.role, c.count])
    return buffer.getvalue()


RENDERERS = {"table": render_table, "markdown": render_markdown, "json": render_json, "csv": render_csv}


def render(report: Report, fmt: str) -> str:
    return RENDERERS[fmt](report)
