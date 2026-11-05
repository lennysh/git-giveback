import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from tests.helpers import *  # noqa: F401,F403
from git_giveback import cli
from git_giveback.models import AccountResult, Contribution
from git_giveback.output import Report, render, score_account
from git_giveback.points import PointsError, default_points, parse_points
from git_giveback.scopes import parse_scopes

UTC = timezone.utc
WHEN = datetime(2026, 4, 1, tzinfo=UTC)


def account():
    return AccountResult("github", "github.com", "me", contributions=[
        Contribution("pull_request", "redhat-cop/a", "A", "https://x/1", WHEN, "merged", "authored", "redhat-cop/a#1"),
        Contribution("pull_request", "me/b", "B", "https://x/2", WHEN, "merged", "authored", "me/b#2"),
        Contribution("pull_request", "me/b", "C", "https://x/3", WHEN, "open", "authored", "me/b#3"),
        Contribution("review", "redhat-cop/a", "D", "https://x/4", WHEN, "approved", "reviewer", "redhat-cop/a#4"),
        Contribution("issue", "me/b", "E", "https://x/5", WHEN, "closed", "assignee", "me/b#5"),
        Contribution("issue", "me/b", "F", "https://x/6", WHEN, "closed", "authored, assignee", "me/b#6"),
        Contribution("commits", "redhat-cop/a", "", "https://x/c", WHEN, "", "authored", "redhat-cop/a", count=4),
    ])


def report(points, scopes=None):
    return Report(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC), UTC, [account()], points, scopes)


class PointsTest(unittest.TestCase):
    def test_default_rules_match_original_program(self):
        score = score_account(account(), report(default_points()))
        self.assertEqual(score.total, 4)  # 2 merged PRs, 1 review, 1 assignee-closed issue (self-opened excluded)
        self.assertEqual([(t.rule.description, t.matched) for t in score.rules],
                         [("Merged pull requests", 2), ("Pull requests reviewed", 1), ("Issues closed as assignee", 1)])

    def test_scope_rules_per_commit_and_season(self):
        scopes = parse_scopes({"scope": [{"name": "CoP", "host": "github.com", "owner": "redhat-cop"}]})
        points = parse_points({"rule": [
            {"kind": "pull_request", "state": "merged", "scope": "CoP", "points": 5},
            {"kind": "commits", "points": 0.5, "per_commit": True},
        ]})
        self.assertEqual(score_account(account(), report(points, scopes)).total, 7)  # 5 + 4 * 0.5
        self.assertEqual(score_account(account(), report(points)).total, 2)  # no scopes: scope rule can't match
        seasonal = parse_points({"since": "2026-06-01", "rule": [{"kind": "review", "points": 1}]})
        self.assertEqual(score_account(account(), report(seasonal)).total, 0)

    def test_invalid(self):
        for bad in ({"rule": [{"kind": "nope", "points": 1}]}, {"rule": [{"kind": "review", "points": True}]},
                    {"rule": [{"kind": "review", "points": 1, "extra": 1}]}, {"until": "later"}):
            with self.assertRaises(PointsError):
                parse_points(bad)

    def test_renderers(self):
        text = render(report(default_points()), "table")
        self.assertIn("· 4 points", text)
        self.assertIn("Points (4)", text)
        self.assertIn("Merged pull requests       2 × 1 = 2", text)
        self.assertIn("### Points (4)", render(report(default_points()), "markdown"))
        data = json.loads(render(report(default_points()), "json"))["accounts"][0]
        self.assertEqual(data["points"]["total"], 4)
        self.assertEqual(data["contributions"][0]["points"], 1)
        self.assertIn(",1\r\n", render(report(default_points()), "csv"))
        self.assertNotIn("Points", render(report(None), "table"))


class FakeProvider:
    def collect(self, since, until):
        return account()


class PointsCliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["--github", *argv], provider_factory=lambda a, t, v: FakeProvider())
        return out.getvalue()

    def test_points_file_and_no_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "points.toml")
            with open(path, "w") as handle:
                handle.write('[[rule]]\nlabel = "Everything merged"\nkind = "pull_request"\nstate = "merged"\npoints = 10\n')
            self.assertIn("Everything merged  2 × 10 = 20", self.run_cli("--points", path))
        self.assertNotIn("Points", self.run_cli("--no-points"))
        self.assertIn("Points (4)", self.run_cli())
