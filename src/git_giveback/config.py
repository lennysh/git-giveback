"""Account configuration and token discovery.

Accounts come from (first match wins):
  1. ``--github`` / ``--gitlab`` command-line flags
  2. the TOML config file (``~/.config/git-giveback/config.toml``)
  3. auto-detection: github.com if a GitHub token is found, GitLab if
     ``GITLAB_TOKEN`` is set (``GITLAB_URL`` picks the instance)
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlparse

from .models import ProviderError


@dataclass
class Account:
    provider: str  # "github" | "gitlab"
    host: str  # GitHub hostname, or GitLab base URL
    username: str | None = None
    token: str | None = None
    token_env: str | None = None


def default_config_path(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "git-giveback" / "config.toml"


def load_config(path: str | None, env: Mapping[str, str] = os.environ) -> dict:
    target = Path(path).expanduser() if path else default_config_path(env)
    if not target.exists():
        if path:
            raise ProviderError(f"config file not found: {target}")
        return {}
    try:
        with target.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as err:
        raise ProviderError(f"invalid config file {target}: {err}") from None


def _normalize_gitlab_url(url: str) -> str:
    url = url.rstrip("/")
    return url if "://" in url else f"https://{url}"


def accounts_from_config(config: dict) -> list[Account]:
    accounts = []
    for entry in config.get("github", []):
        accounts.append(Account("github", entry.get("host", "github.com"), entry.get("username"),
                                entry.get("token"), entry.get("token_env")))
    for entry in config.get("gitlab", []):
        accounts.append(Account("gitlab", _normalize_gitlab_url(entry.get("url", "https://gitlab.com")),
                                entry.get("username"), entry.get("token"), entry.get("token_env")))
    return accounts


def accounts_from_args(github: list[str] | None, gitlab: list[str] | None, user: str | None) -> list[Account]:
    accounts = [Account("github", host, user) for host in github or []]
    accounts += [Account("gitlab", _normalize_gitlab_url(url), user) for url in gitlab or []]
    return accounts


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token else None


def resolve_token(account: Account, env: Mapping[str, str] = os.environ,
                  runner: Callable[[list[str]], str | None] = _run) -> str | None:
    if account.token:
        return account.token
    if account.token_env:
        return env.get(account.token_env) or None

    if account.provider == "github":
        names = ("GH_TOKEN", "GITHUB_TOKEN") if account.host == "github.com" \
            else ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
        for name in names:
            if env.get(name):
                return env[name]
        return runner(["gh", "auth", "token", "--hostname", account.host])

    if env.get("GITLAB_TOKEN"):
        return env["GITLAB_TOKEN"]
    host = urlparse(account.host).netloc
    return runner(["glab", "config", "get", "token", "--host", host])


def auto_detect(env: Mapping[str, str] = os.environ,
                runner: Callable[[list[str]], str | None] = _run) -> list[Account]:
    accounts = []
    github = Account("github", "github.com")
    if resolve_token(github, env, runner):
        accounts.append(github)
    if env.get("GITLAB_TOKEN"):
        accounts.append(Account("gitlab", _normalize_gitlab_url(env.get("GITLAB_URL", "https://gitlab.com"))))
    return accounts
