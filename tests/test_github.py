import unittest
from datetime import datetime, timezone

from tests.helpers import FakeClient
from git_giveback.providers.github import GitHubProvider

UTC = timezone.utc
SINCE, UNTIL = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)


def pr(number, merged=False, state="CLOSED", draft=False, merged_at=None, created="2026-01-05T00:00:00Z"):
    return {"__typename": "PullRequest", "number": number, "title": f"PR {number}",
            "url": f"https://github.com/o/r/pull/{number}", "state": state, "isDraft": draft,
            "merged": merged, "mergedAt": merged_at, "createdAt": created, "closedAt": None,
            "repository": {"nameWithOwner": "o/r"}}


def issue(number, created="2026-01-03T00:00:00Z", closed=None):
    return {"__typename": "Issue", "number": number, "title": f"Issue {number}",
            "url": f"https://github.com/o/r/issues/{number}", "state": "CLOSED" if closed else "OPEN",
            "createdAt": created, "closedAt": closed, "repository": {"nameWithOwner": "o/r"}}


def search(nodes, count=None):
    return {"data": {"search": {"issueCount": len(nodes) if count is None else count,
                                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}


class GitHubHandler:
    def __init__(self, searches=None, reviews=None, commits=None, errors=None):
        self.searches = searches or {}
        self.reviews = reviews or []
        self.commits = commits or []
        self.errors = errors
        self.queries = []

    def __call__(self, method, url, params, body):
        query, variables = body["query"], body["variables"]
        if "viewer" in query:
            return {"data": {"viewer": {"login": "me"}}}, None
        if "search(" in query:
            self.queries.append(variables["q"])
            for prefix, response in self.searches.items():
                if variables["q"].startswith(prefix):
                    return (response(variables["q"]) if callable(response) else response), None
            return search([]), None
        if "pullRequestReviewContributions" in query:
            conn = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.reviews}
            return {"data": {"user": {"contributionsCollection": {"pullRequestReviewContributions": conn}}}}, None
        if "commitContributionsByRepository" in query:
            payload = {"data": {"user": {"contributionsCollection": {
                "restrictedContributionsCount": 2, "commitContributionsByRepository": self.commits}}}}
            if self.errors:
                payload["errors"] = self.errors
            return payload, None
        raise AssertionError(query)


class GitHubProviderTest(unittest.TestCase):
    def collect(self, handler, **kwargs):
        provider = GitHubProvider(client=FakeClient(handler), **kwargs)
        return provider.collect(SINCE, UNTIL)

    def test_pull_requests_states_and_dedup(self):
        handler = GitHubHandler(searches={
            "is:pr author:me created": search([pr(1, merged=True, merged_at="2026-01-10T00:00:00Z"),
                                                pr(2, state="OPEN", draft=True), pr(3, state="OPEN")]),
            "is:pr author:me merged": search([pr(1, merged=True, merged_at="2026-01-10T00:00:00Z"),
                                               pr(4, merged=True, merged_at="2026-01-20T00:00:00Z",
                                                  created="2025-12-01T00:00:00Z")]),
        })
        result = self.collect(handler)
        prs = {c.ref: c for c in result.contributions if c.kind == "pull_request"}
        self.assertEqual({k: v.state for k, v in prs.items()},
                         {"o/r#1": "merged", "o/r#2": "draft", "o/r#3": "open", "o/r#4": "merged"})
        self.assertEqual(prs["o/r#4"].date, datetime(2026, 1, 20, tzinfo=UTC))  # merge date, not creation
        self.assertEqual(result.user, "me")
        self.assertIn("created:2026-01-01T00:00:00Z..2026-01-31T23:59:59Z", handler.queries[0])

    def test_issues_merge_roles(self):
        handler = GitHubHandler(searches={
            "is:issue author:me": search([issue(7), issue(8)]),
            "is:issue is:closed assignee:me": search([issue(8, closed="2026-01-09T00:00:00Z"),
                                                      issue(9, created="2025-01-01T00:00:00Z",
                                                            closed="2026-01-15T00:00:00Z")]),
        })
        issues = {c.ref: c for c in self.collect(handler).contributions if c.kind == "issue"}
        self.assertEqual(issues["o/r#8"].role, "authored, assignee")
        self.assertEqual(issues["o/r#9"].date, datetime(2026, 1, 15, tzinfo=UTC))

    def test_search_splits_when_over_limit(self):
        def big(query):
            # Pretend the whole month has too many results; each half fits.
            return search([pr(1)], count=1500) if "2026-01-01T00:00:00Z..2026-01-31" in query else search([])
        handler = GitHubHandler(searches={"is:pr author:me created": big})
        self.collect(handler)
        created = [q for q in handler.queries if q.startswith("is:pr author:me created")]
        self.assertEqual(len(created), 3)

    def test_reviews_dedup_and_skip_own(self):
        def review(n, state, at, author="other"):
            return {"occurredAt": at, "pullRequestReview": {"state": state},
                    "pullRequest": {"number": n, "title": f"PR {n}", "url": f"https://github.com/o/r/pull/{n}",
                                    "repository": {"nameWithOwner": "o/r"}, "author": {"login": author}}}
        handler = GitHubHandler(reviews=[
            review(5, "COMMENTED", "2026-01-02T00:00:00Z"),
            review(5, "APPROVED", "2026-01-04T00:00:00Z"),
            review(6, "COMMENTED", "2026-01-03T00:00:00Z", author="me"),
        ])
        reviews = [c for c in self.collect(handler).contributions if c.kind == "review"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual((reviews[0].state, reviews[0].count), ("approved", 2))
        self.assertEqual(reviews[0].date, datetime(2026, 1, 4, tzinfo=UTC))

    def test_commits_and_partial_errors(self):
        handler = GitHubHandler(
            commits=[{"repository": {"nameWithOwner": "o/r", "url": "https://github.com/o/r"},
                      "contributions": {"nodes": [{"occurredAt": "2026-01-02T08:00:00Z", "commitCount": 3},
                                                  {"occurredAt": "2026-01-05T08:00:00Z", "commitCount": 2}]}}],
            errors=[{"message": "Resource protected by organization SAML enforcement."}],
        )
        result = self.collect(handler, username="me")
        commits = sorted((c.date, c.count) for c in result.contributions if c.kind == "commits")
        self.assertEqual(commits, [(datetime(2026, 1, 2, 8, tzinfo=UTC), 3), (datetime(2026, 1, 5, 8, tzinfo=UTC), 2)])
        self.assertTrue(any("SAML" in n for n in result.notes))
        self.assertTrue(any("2 contributions" in n for n in result.notes))
        self.assertEqual(result.errors, [])
