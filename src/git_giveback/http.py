"""Tiny JSON-over-HTTP client built on urllib, with rate-limit aware retries."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

USER_AGENT = f"git-giveback/{__version__}"
MAX_RETRIES = 3
MAX_WAIT_SECONDS = 120


class HttpError(Exception):
    def __init__(self, status: int, url: str, message: str):
        super().__init__(f"HTTP {status} from {url}: {message}" if status else f"{url}: {message}")
        self.status = status
        self.url = url


def parse_link_header(value: str | None) -> dict[str, str]:
    links: dict[str, str] = {}
    for part in (value or "").split(","):
        pieces = part.split(";")
        if len(pieces) < 2:
            continue
        url = pieces[0].strip().strip("<>")
        for param in pieces[1:]:
            param = param.strip()
            if param.startswith("rel="):
                links[param[4:].strip('"')] = url
    return links


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: str):
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body

    def json(self):
        return json.loads(self.body) if self.body else None

    @property
    def links(self) -> dict[str, str]:
        return parse_link_header(self.headers.get("link"))


def retry_wait(status: int, headers: dict[str, str], attempt: int, now: float | None = None) -> float | None:
    """Seconds to wait before retrying, or None if the request should not be retried."""
    headers = {k.lower(): v for k, v in headers.items()}
    now = time.time() if now is None else now
    if status in (502, 503, 504):
        return float(2**attempt)
    if status not in (403, 429):
        return None
    if headers.get("retry-after", "").isdigit():
        wait = float(headers["retry-after"])
    elif headers.get("x-ratelimit-remaining") == "0" and headers.get("x-ratelimit-reset", "").isdigit():
        wait = max(0.0, float(headers["x-ratelimit-reset"]) - now) + 1
    elif status == 429:
        wait = float(2 ** (attempt + 2))
    else:
        return None  # plain 403: a permissions problem, not a rate limit
    return wait if wait <= MAX_WAIT_SECONDS else None


def _summarize(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:300] or "(empty response)"
    if isinstance(data, dict):
        for key in ("message", "error_description", "error"):
            if data.get(key):
                return str(data[key])
    return body.strip()[:300]


class HttpClient:
    def __init__(self, headers: dict[str, str] | None = None, timeout: float = 60, verbose: bool = False):
        self.headers = headers or {}
        self.timeout = timeout
        self.verbose = verbose

    def request(self, method: str, url: str, params: dict | None = None, json_body=None) -> Response:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(json_body).encode() if json_body is not None else None
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **self.headers}
        if data is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(MAX_RETRIES + 1):
            if self.verbose:
                print(f"  {method} {url}", file=sys.stderr)
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return Response(resp.status, dict(resp.headers.items()), resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", errors="replace")
                err_headers = dict(err.headers.items()) if err.headers else {}
                wait = retry_wait(err.code, err_headers, attempt)
                if wait is not None and attempt < MAX_RETRIES:
                    print(f"  rate limited or server busy; retrying in {wait:.0f}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise HttpError(err.code, url.split("?")[0], _summarize(body)) from None
            except urllib.error.URLError as err:
                raise HttpError(0, url.split("?")[0], str(err.reason)) from None
        raise AssertionError("unreachable")
