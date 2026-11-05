"""GitHub / GitHub Enterprise provider (GraphQL API).

* Commits and reviews come from ``contributionsCollection`` (the same data as
  the profile contribution graph). It accepts at most one year per query, and
  commits are queried in 90-day windows so each repo has fewer than 100 day
  buckets and needs no nested pagination.
* Pull requests and issues come from the search API, which can date-filter
  by created/merged/closed. Search returns at most 1000 results per query, so
  busy windows are split in half until they fit.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..dates import chunks, in_range, parse_api_datetime, to_utc_iso
from ..http import HttpClient
from ..models import AccountResult, Contribution, ProviderError
from . import run_section

COMMIT_WINDOW_DAYS = 90
YEAR_WINDOW_DAYS = 365
SEARCH_LIMIT = 1000
ONE_SECOND = timedelta(seconds=1)

VIEWER_QUERY = "query { viewer { login } }"

COMMITS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      restrictedContributionsCount
      commitContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner url }
        contributions(first: 100) { nodes { occurredAt commitCount } }
      }
    }
  }
}
"""

REVIEWS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!, $after: String) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      pullRequestReviewContributions(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          occurredAt
          pullRequestReview { state }
          pullRequest { number title url repository { nameWithOwner } author { login } }
        }
      }
    }
  }
}
"""

SEARCH_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      __typename
      ... on PullRequest {
        number title url state isDraft merged mergedAt createdAt closedAt
        repository { nameWithOwner }
      }
      ... on Issue {
        number title url state createdAt closedAt
        repository { nameWithOwner }
      }
    }
  }
}
"""

REVIEW_RANK = {"APPROVED": 3, "CHANGES_REQUESTED": 2, "COMMENTED": 1}


def graphql_endpoint(host: str) -> str:
    return "https://api.github.com/graphql" if host == "github.com" else f"https://{host}/api/graphql"


class GitHubProvider:
    provider = "github"

    def __init__(self, host: str = "github.com", token: str | None = None,
                 username: str | None = None, client=None, verbose: bool = False):
        if client is None:
            if not token:
                raise ProviderError(f"no token available for {host}")
            client = HttpClient(headers={"Authorization": f"bearer {token}"}, verbose=verbose)
        self.host = host
        self.username = username
        self.client = client
        self.endpoint = graphql_endpoint(host)
        self._result: AccountResult | None = None

    # -- plumbing -----------------------------------------------------------

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        payload = self.client.request(
            "POST", self.endpoint, json_body={"query": query, "variables": variables or {}}
        ).json() or {}
        data = payload.get("data")
        messages = sorted({e.get("message", "unknown error") for e in payload.get("errors") or []})
        if messages:
            if data is None or any("rate limit" in m.lower() for m in messages):
                raise ProviderError("; ".join(messages))
            # Partial data, e.g. SAML-protected orgs: keep going but tell the user.
            for message in messages:
                self._result.add_note(message)
        return data or {}

    def _user(self, data: dict, login: str) -> dict:
        user = data.get("user")
        if user is None:
            raise ProviderError(f"GitHub user '{login}' not found on {self.host}")
        return user

    def _search(self, qualifiers: str, field: str, start: datetime, end: datetime) -> list[dict]:
        query = f"{qualifiers} {field}:{to_utc_iso(start)}..{to_utc_iso(end - ONE_SECOND)}"
        nodes: list[dict] = []
        after = None
        while True:
            search = self._graphql(SEARCH_QUERY, {"q": query, "after": after})["search"]
            if after is None and search["issueCount"] > SEARCH_LIMIT:
                if end - start > timedelta(days=1):
                    middle = start + (end - start) / 2
                    return self._search(qualifiers, field, start, middle) + \
                        self._search(qualifiers, field, middle, end)
                self._result.add_note(
                    f"search '{query}' matched {search['issueCount']} items; only the first {SEARCH_LIMIT} are listed"
                )
            nodes.extend(n for n in search["nodes"] if n)
            if not search["pageInfo"]["hasNextPage"]:
                return nodes
            after = search["pageInfo"]["endCursor"]

    # -- collection ---------------------------------------------------------

    def collect(self, since: datetime, until: datetime) -> AccountResult:
        login = self.username or self._viewer_login()
        result = self._result = AccountResult(provider=self.provider, host=self.host, user=login)
        run_section(result, "pull requests", lambda: self._pull_requests(login, since, until))
        run_section(result, "reviews", lambda: self._reviews(login, since, until))
        run_section(result, "issues", lambda: self._issues(login, since, until))
        run_section(result, "commits", lambda: self._commits(login, since, until))
        return result

    def _viewer_login(self) -> str:
        self._result = AccountResult(provider=self.provider, host=self.host, user="?")
        return self._graphql(VIEWER_QUERY)["viewer"]["login"]

    def _commits(self, login: str, since: datetime, until: datetime) -> list[Contribution]:
        """One Contribution per repository per day (GitHub buckets commit contributions by day)."""
        per_day: dict[tuple[str, datetime], Contribution] = {}
        hidden = 0
        for start, end in chunks(since, until, COMMIT_WINDOW_DAYS):
            data = self._graphql(COMMITS_QUERY, {
                "login": login, "from": to_utc_iso(start), "to": to_utc_iso(end - ONE_SECOND),
            })
            collection = self._user(data, login)["contributionsCollection"]
            hidden += collection.get("restrictedContributionsCount") or 0
            for entry in collection["commitContributionsByRepository"]:
                repo = entry["repository"]
                name = repo["nameWithOwner"]
                for node in entry["contributions"]["nodes"]:
                    day = parse_api_datetime(node["occurredAt"])
                    if not node["commitCount"] or day is None:
                        continue
                    item = per_day.setdefault((name, day), Contribution(
                        kind="commits", repo=name, title="", ref=name,
                        url=f"{repo['url']}/commits?author={login}", date=day, role="authored", count=0,
                    ))
                    item.count += node["commitCount"]
        if hidden:
            self._result.add_note(
                f"{hidden} contributions are in private repositories this token cannot see "
                "(check token scopes and SAML SSO authorization)"
            )
        return list(per_day.values())

    def _reviews(self, login: str, since: datetime, until: datetime) -> list[Contribution]:
        per_pr: dict[str, Contribution] = {}
        for start, end in chunks(since, until, YEAR_WINDOW_DAYS):
            after = None
            while True:
                data = self._graphql(REVIEWS_QUERY, {
                    "login": login, "from": to_utc_iso(start), "to": to_utc_iso(end - ONE_SECOND),
                    "after": after,
                })
                conn = self._user(data, login)["contributionsCollection"]["pullRequestReviewContributions"]
                for node in conn["nodes"]:
                    pr = node.get("pullRequest")
                    if not pr or (pr.get("author") or {}).get("login") == login:
                        continue
                    state = (node.get("pullRequestReview") or {}).get("state", "COMMENTED")
                    occurred = parse_api_datetime(node["occurredAt"])
                    item = per_pr.get(pr["url"])
                    if item is None:
                        repo = pr["repository"]["nameWithOwner"]
                        per_pr[pr["url"]] = Contribution(
                            kind="review", repo=repo, title=pr["title"], url=pr["url"], date=occurred,
                            state=state.lower().replace("_", " "), role="reviewer", ref=f"{repo}#{pr['number']}",
                        )
                        continue
                    item.count += 1
                    if occurred and (item.date is None or occurred > item.date):
                        item.date = occurred
                    if REVIEW_RANK.get(state, 0) > REVIEW_RANK.get(item.state.upper().replace(" ", "_"), 0):
                        item.state = state.lower().replace("_", " ")
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                after = conn["pageInfo"]["endCursor"]
        return list(per_pr.values())

    def _pull_requests(self, login: str, since: datetime, until: datetime) -> list[Contribution]:
        found: dict[str, Contribution] = {}
        for field in ("created", "merged"):
            for node in self._search(f"is:pr author:{login}", field, since, until):
                if node.get("__typename") != "PullRequest" or node["url"] in found:
                    continue
                merged_at = parse_api_datetime(node.get("mergedAt"))
                if node.get("merged"):
                    state = "merged"
                elif node["state"] == "OPEN":
                    state = "draft" if node.get("isDraft") else "open"
                else:
                    state = "closed"
                repo = node["repository"]["nameWithOwner"]
                found[node["url"]] = Contribution(
                    kind="pull_request", repo=repo, title=node["title"], url=node["url"],
                    date=merged_at if in_range(merged_at, since, until) else parse_api_datetime(node["createdAt"]),
                    state=state, role="authored", ref=f"{repo}#{node['number']}",
                )
        return list(found.values())

    def _issues(self, login: str, since: datetime, until: datetime) -> list[Contribution]:
        found: dict[str, Contribution] = {}
        searches = (
            (f"is:issue author:{login}", "created", "authored", "createdAt"),
            (f"is:issue is:closed assignee:{login}", "closed", "assignee", "closedAt"),
        )
        for qualifiers, field, role, date_key in searches:
            for node in self._search(qualifiers, field, since, until):
                if node.get("__typename") != "Issue":
                    continue
                if node["url"] in found:
                    existing = found[node["url"]]
                    if role not in existing.role:
                        existing.role += f", {role}"
                    continue
                repo = node["repository"]["nameWithOwner"]
                found[node["url"]] = Contribution(
                    kind="issue", repo=repo, title=node["title"], url=node["url"],
                    date=parse_api_datetime(node.get(date_key)), state=node["state"].lower(),
                    role=role, ref=f"{repo}#{node['number']}",
                )
        return list(found.values())
