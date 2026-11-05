"""Command-line entry point."""

from __future__ import annotations

import argparse
import re
import sys
from typing import Callable

from . import __version__
from .config import Account, accounts_from_args, accounts_from_config, auto_detect, load_config, resolve_token
from .dates import display_tz, format_range, resolve_range
from .http import HttpError
from .models import KINDS, AccountResult, ProviderError
from .output import FORMATS, Report, render
from .providers.github import GitHubProvider
from .providers.gitlab import GitLabProvider

EPILOG = """\
examples:
  git-giveback --since 2026-01-01 --until 2026-06-30
  git-giveback --days 90 --format markdown -o q3-giveback.md
  git-giveback --github --gitlab https://gitlab.example.com --since 2026-03-01
  git-giveback --exclude '^my-user/' --type pull_request --type review

Tokens are read from GH_TOKEN/GITHUB_TOKEN (or `gh auth token`) and
GITLAB_TOKEN (or `glab`). See README.md for the config file format.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-giveback",
        description="List your GitHub and GitLab contributions for a date range.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    when = parser.add_argument_group("date range (default: the last 30 days)")
    start = when.add_mutually_exclusive_group()
    start.add_argument("-s", "--since", metavar="DATE", help="start date, YYYY-MM-DD or ISO 8601 datetime")
    start.add_argument("-d", "--days", type=int, metavar="N", help="the N days before --until")
    when.add_argument("-u", "--until", metavar="DATE", help="end date, inclusive when a plain date (default: now)")
    when.add_argument("--utc", action="store_true", help="interpret and display dates in UTC instead of local time")

    accounts = parser.add_argument_group("accounts (default: config file, then auto-detect)")
    accounts.add_argument("--github", action="append", nargs="?", const="github.com", metavar="HOST",
                          help="query a GitHub host (default github.com); repeatable")
    accounts.add_argument("--gitlab", action="append", nargs="?", const="https://gitlab.com", metavar="URL",
                          help="query a GitLab instance (default https://gitlab.com); repeatable")
    accounts.add_argument("--user", help="report on this username instead of the token owner "
                                         "(only public activity is visible)")
    accounts.add_argument("-c", "--config", metavar="PATH", help="config file (default ~/.config/git-giveback/config.toml)")

    filters = parser.add_argument_group("filters")
    filters.add_argument("-i", "--include", action="append", metavar="REGEX", help="only repos matching REGEX")
    filters.add_argument("-x", "--exclude", action="append", metavar="REGEX", help="skip repos matching REGEX")
    filters.add_argument("-t", "--type", action="append", choices=KINDS, help="only these contribution types")

    out = parser.add_argument_group("output")
    out.add_argument("-f", "--format", choices=FORMATS, default="table")
    out.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE")
    out.add_argument("-v", "--verbose", action="store_true", help="log API requests to stderr")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def make_provider(account: Account, token: str | None, verbose: bool):
    if account.provider == "github":
        return GitHubProvider(account.host, token, account.username, verbose=verbose)
    return GitLabProvider(account.host, token, account.username, verbose=verbose)


def select_accounts(args) -> list[Account]:
    accounts = accounts_from_args(args.github, args.gitlab, args.user)
    if not accounts:
        accounts = accounts_from_config(load_config(args.config))
        if args.user:
            for account in accounts:
                account.username = args.user
    if not accounts:
        accounts = auto_detect()
        for account in accounts:
            account.username = args.user
    return accounts


def apply_filters(result: AccountResult, include, exclude, types) -> None:
    inc = [re.compile(p) for p in include or []]
    exc = [re.compile(p) for p in exclude or []]
    result.contributions = [
        c for c in result.contributions
        if (not types or c.kind in types)
        and (not inc or any(p.search(c.repo) for p in inc))
        and not any(p.search(c.repo) for p in exc)
    ]


def main(argv: list[str] | None = None, provider_factory: Callable = make_provider) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    tz = display_tz(args.utc)
    try:
        since, until = resolve_range(args.since, args.until, args.days, tz)
        for pattern in (args.include or []) + (args.exclude or []):
            re.compile(pattern)
        accounts = select_accounts(args)
    except (argparse.ArgumentTypeError, re.error, ProviderError) as err:
        parser.error(str(err))
    if not accounts:
        parser.error("no accounts to query: set GITHUB_TOKEN / GITLAB_TOKEN, log in with `gh auth login`, "
                     "or create a config file (see README.md)")

    print(f"Collecting contributions {format_range(since, until, tz)}", file=sys.stderr)
    results = []
    for account in accounts:
        label = account.host if account.provider == "github" else account.host.split("://")[-1]
        print(f"  {account.provider}: {label} ...", file=sys.stderr)
        try:
            provider = provider_factory(account, resolve_token(account), args.verbose)
            result = provider.collect(since, until)
        except (HttpError, ProviderError) as err:
            result = AccountResult(account.provider, label, account.username or "?", errors=[str(err)])
        apply_filters(result, args.include, args.exclude, args.type)
        results.append(result)

    text = render(Report(since, until, tz, results), args.format)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 1 if any(r.errors for r in results) else 0
