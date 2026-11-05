import unittest
from datetime import datetime, timezone

from tests.helpers import FakeClient
from git_giveback.http import HttpError
from git_giveback.providers.gitlab import GitLabProvider

UTC = timezone.utc
SINCE, UNTIL = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
API = "https://gitlab.example.com/api/v4"
WEB = "https://gitlab.example.com/grp/proj"


def mr(iid, state="merged", created="2026-01-05T00:00:00Z", merged=None, draft=False):
    return {"iid": iid, "title": f"MR {iid}", "web_url": f"{WEB}/-/merge_requests/{iid}", "state": state,
            "created_at": created, "merged_at": merged, "draft": draft}


class Handler:
    def __init__(self, routes):
        self.routes = routes

    def __call__(self, method, url, params, body):
        path = url.replace(API, "").split("?")[0]
        key = path
        if path == "/issues":
            key = "/issues:assignee" if "assignee_id" in params else "/issues:author"
        if key not in self.routes:
            raise HttpError(404, url, "not found")
        route = self.routes[key]
        if isinstance(route, list) and route and isinstance(route[0], tuple):  # paginated
            index = int((params or {}).get("page", 0)) if params else int(url.split("page=")[-1])
            data, nxt = route[index]
            return data, ({"Link": f'<{API}{path}?page={nxt}>; rel="next"'} if nxt is not None else None)
        return route, None


class GitLabProviderTest(unittest.TestCase):
    def test_collect(self):
        events = [
            {"created_at": "2026-01-03T10:00:00Z", "project_id": 1, "action_name": "pushed to",
             "push_data": {"commit_count": 3, "ref_type": "branch"}},
            {"created_at": "2026-01-04T10:00:00Z", "project_id": 1, "action_name": "pushed new",
             "push_data": {"commit_count": 2, "ref_type": "branch"}},
            {"created_at": "2026-01-04T11:00:00Z", "project_id": 1, "action_name": "pushed new",
             "push_data": {"commit_count": 0, "ref_type": "tag"}},
            {"created_at": "2025-12-31T23:00:00Z", "project_id": 1, "action_name": "pushed to",
             "push_data": {"commit_count": 9, "ref_type": "branch"}},  # outside range
            {"created_at": "2026-01-06T10:00:00Z", "project_id": 1, "action_name": "commented on",
             "target_title": "Their MR", "note": {"noteable_type": "MergeRequest", "noteable_iid": 40}},
            {"created_at": "2026-01-07T10:00:00Z", "project_id": 1, "action_name": "approved",
             "target_type": "MergeRequest", "target_iid": 40, "target_title": "Their MR"},
            {"created_at": "2026-01-07T10:00:00Z", "project_id": 1, "action_name": "commented on",
             "target_title": "My MR", "note": {"noteable_type": "MergeRequest", "noteable_iid": 1}},
            {"created_at": "2026-01-08T10:00:00Z", "project_id": 2, "action_name": "accepted",
             "target_type": "MergeRequest", "target_iid": 3, "target_title": "Hidden project MR"},
        ]
        handler = Handler({
            "/user": {"id": 42, "username": "me"},
            "/merge_requests": [([mr(1, merged="2026-01-10T00:00:00Z"), mr(2, state="opened", draft=True)], 1),
                                ([mr(3, state="closed", created="2025-11-01T00:00:00Z")], None)],
            "/issues:author": [{"iid": 5, "title": "Bug", "web_url": f"{WEB}/-/issues/5", "state": "opened",
                                "created_at": "2026-01-02T00:00:00Z"}],
            "/issues:assignee": [{"iid": 5, "title": "Bug", "web_url": f"{WEB}/-/issues/5", "state": "closed",
                                  "created_at": "2026-01-02T00:00:00Z", "closed_at": "2026-01-20T00:00:00Z"}],
            "/users/42/events": events,
            "/projects/1": {"path_with_namespace": "grp/proj", "web_url": WEB},
        })
        provider = GitLabProvider("https://gitlab.example.com/", client=FakeClient(handler))
        result = provider.collect(SINCE, UNTIL)
        by_kind = {}
        for c in result.contributions:
            by_kind.setdefault(c.kind, []).append(c)

        self.assertEqual(result.host, "gitlab.example.com")
        self.assertEqual(sorted((c.ref, c.state) for c in by_kind["pull_request"]),
                         [("grp/proj!1", "merged"), ("grp/proj!2", "draft")])
        self.assertEqual([(c.ref, c.role) for c in by_kind["issue"]], [("grp/proj#5", "authored, assignee")])
        self.assertEqual([(c.repo, c.count) for c in by_kind["commits"]], [("grp/proj", 5)])
        reviews = {c.ref: c for c in by_kind["review"]}
        self.assertEqual(set(reviews), {"grp/proj!40", "project-2!3"})  # own MR !1 excluded
        self.assertEqual((reviews["grp/proj!40"].state, reviews["grp/proj!40"].count), ("approved", 2))
        self.assertEqual(reviews["project-2!3"].state, "merged")
        self.assertEqual(result.errors, [])

    def test_unknown_username(self):
        provider = GitLabProvider("https://gitlab.example.com", username="ghost",
                                  client=FakeClient(Handler({"/users": []})))
        with self.assertRaisesRegex(Exception, "ghost"):
            provider.collect(SINCE, UNTIL)
