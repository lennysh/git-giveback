"""Contribution providers (one per forge type)."""

from __future__ import annotations

from typing import Callable

from ..http import HttpError
from ..models import AccountResult, Contribution, ProviderError


def run_section(result: AccountResult, label: str, fn: Callable[[], list[Contribution]]) -> None:
    """Run one collection step; record failures instead of losing the whole account."""
    try:
        result.contributions.extend(fn())
    except (HttpError, ProviderError) as err:
        result.errors.append(f"{label}: {err}")
