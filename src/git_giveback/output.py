"""Report renderers: table (terminal), markdown, json and csv."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, tzinfo

from .dates import format_range
from .models import KINDS, AccountResult, Contribution
from .points import Points, Rule
from .scopes import Scope, scope_for

FORMATS = ("table", "markdown", "json", "csv")
PR_STATE_ORDER = ("merged", "open", "draft", "closed", "locked")


@dataclass
class Report:
    since: datetime
    until: datetime
    tz: tzinfo
    accounts: list[AccountResult]
    points: Points | None = None  # None: points disabled
    scopes: list[Scope] | None = None  # lets point rules match on scope names


@dataclass
class RuleTally:
    rule: Rule
    matched: int = 0  # contributions (or commits, for per_commit rules)
    points: float = 0


@dataclass
class Score:
    total: float
    rules: list[RuleTally]
    per_item: dict[int, float]  # id(contribution) -> points

    def of(self, item: Contribution) -> float:
        return self.per_item.get(id(item), 0)


def score_account(account: AccountResult, report: Report) -> Score | None:
    if report.points is None:
        return None
    tallies = {id(rule): RuleTally(rule) for rule in report.points.rules}
    per_item: dict[int, float] = {}
    for item in account.contributions:
        scope = scope_for(report.scopes, account.host, item.repo, item.kind) if report.scopes else None
        day = item.date.astimezone(report.tz).date() if item.date else None
        rule = report.points.match(item.kind, item.state, item.role, scope.name if scope else None, day)
        if rule is None:
            continue
        units = item.count if rule.per_commit else 1
        value = rule.points * units
        per_item[id(item)] = value
        tallies[id(rule)].matched += units
        tallies[id(rule)].points += value
    return Score(sum(per_item.values()), list(tallies.values()), per_item)


def fmt_points(value: float) -> str:
    return f"{value:g}"


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def kind_title(kind: str, provider: str) -> str:
    return {
        "pull_request": "Merge requests" if provider == "gitlab" else "Pull requests",
        "review": "Reviews",
        "issue": "Issues",
        "commits": "Pushed commits" if provider == "gitlab" else "Commits",
    }[kind]


def rollup_commits(items: list[Contribution]) -> list[Contribution]:
    """Combine per-day commit rows into one row per repository (latest date, summed count)."""
    per_repo: dict[tuple[str, str], Contribution] = {}
    for item in items:
        key = (item.repo, item.url)
        if key not in per_repo:
            per_repo[key] = replace(item, count=0)
        total = per_repo[key]
        total.count += item.count
        if item.date and (total.date is None or item.date > total.date):
            total.date = item.date
    return list(per_repo.values())


def grouped(contributions: list[Contribution]) -> dict[str, list[Contribution]]:
    """Group by kind for display; commits are rolled up per repository."""
    groups: dict[str, list[Contribution]] = {kind: [] for kind in KINDS}
    for item in contributions:
        groups.setdefault(item.kind, []).append(item)
    groups["commits"] = rollup_commits(groups["commits"])
    for items in groups.values():
        items.sort(key=lambda c: (c.date is not None, c.date), reverse=True)
    return groups


def summary(account: AccountResult, score: Score | None = None) -> str:
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
        parts.append(plural(len(groups["issue"]), "issue"))
    if groups["commits"]:
        total = sum(c.count for c in groups["commits"])
        noun = "pushed commits" if account.provider == "gitlab" else "commits"
        parts.append(f"{total} {noun} in {plural(len(groups['commits']), 'repo')}")
    if score is not None and parts:
        parts.append(f"{fmt_points(score.total)} points")
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
        score = score_account(account, report)
        lines.append(f"{account.host} · @{account.user}")
        lines.append(f"  {summary(account, score)}")
        for kind, items in grouped(account.contributions).items():
            if not items:
                continue
            lines += ["", f"  {kind_title(kind, account.provider)} ({len(items)})"]
            label_w = max(len(_label(c)) for c in items)
            ref_w = max(len(c.ref) for c in items)
            for c in items:
                row = f"    {_date(c, report.tz)}  {_label(c):<{label_w}}  {c.ref:<{ref_w}}  {c.title}"
                lines.append(row.rstrip())
        if score is not None and account.contributions:
            lines += ["", f"  Points ({fmt_points(score.total)})"]
            if not score.rules:
                lines.append("    (no scoring rules)")
            label_w = max((len(t.rule.description) for t in score.rules), default=0)
            count_w = max((len(str(t.matched)) for t in score.rules), default=1)
            for t in score.rules:
                lines.append(f"    {t.rule.description:<{label_w}}  {t.matched:>{count_w}} × "
                             f"{fmt_points(t.rule.points)} = {fmt_points(t.points)}")
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
        score = score_account(account, report)
        lines += [f"## {account.host} — @{account.user}", "", f"**Summary:** {summary(account, score)}", ""]
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
        if score is not None and account.contributions:
            lines += [f"### Points ({fmt_points(score.total)})", "", "| Rule | Count | Points |", "|---|---:|---:|"]
            lines += [f"| {md_escape(t.rule.description)} | {t.matched} × {fmt_points(t.rule.points)} | "
                      f"{fmt_points(t.points)} |" for t in score.rules]
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
        "accounts": [],
    }
    for a in report.accounts:
        score = score_account(a, report)
        entry = {"provider": a.provider, "host": a.host, "user": a.user, "summary": summary(a, score)}
        entry["contributions"] = [
            {**c.to_dict(), **({"points": score.of(c)} if score else {})} for c in a.contributions
        ]
        if score is not None:
            entry["points"] = {"total": score.total, "rules": [
                {"rule": t.rule.description, "matched": t.matched, "points": t.points} for t in score.rules
            ]}
        entry.update(notes=a.notes, errors=a.errors)
        data["accounts"].append(entry)
    return json.dumps(data, indent=2) + "\n"


CSV_FIELDS = ("provider", "host", "user", "kind", "repo", "ref", "title", "url", "date", "state", "role", "count",
              "points")


def render_csv(report: Report) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_FIELDS)
    for account in report.accounts:
        score = score_account(account, report)
        for c in account.contributions:
            writer.writerow([account.provider, account.host, account.user, c.kind, c.repo, c.ref, c.title,
                             c.url, c.date.isoformat() if c.date else "", c.state, c.role, c.count,
                             fmt_points(score.of(c)) if score else ""])
    return buffer.getvalue()


RENDERERS = {"table": render_table, "markdown": render_markdown, "json": render_json, "csv": render_csv}


def render(report: Report, fmt: str) -> str:
    return RENDERERS[fmt](report)
