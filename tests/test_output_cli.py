import contextlib
import csv
import io
import json
import unittest
from datetime import datetime, timezone

from tests.helpers import *  # noqa: F401,F403
from git_giveback import cli
from git_giveback.models import AccountResult, Contribution
from git_giveback.output import Report, md_escape, render, summary

UTC = timezone.utc


def sample_account():
    when = datetime(2026, 1, 5, tzinfo=UTC)
    return AccountResult("github", "github.com", "me", contributions=[
        Contribution("pull_request", "o/r", "Fix [bug] *now*", "https://x/1", when, "merged", "authored", "o/r#1"),
        Contribution("pull_request", "o/r", "WIP", "https://x/2", when, "open", "authored", "o/r#2"),
        Contribution("review", "o/s", "Their PR", "https://x/3", when, "approved", "reviewer", "o/s#3"),
        Contribution("commits", "o/r", "", "https://x/c", when, "", "authored", "o/r", count=10),
        Contribution("commits", "o/r", "", "https://x/c", datetime(2026, 1, 2, tzinfo=UTC), "", "authored",
                     "o/r", count=2),
    ], notes=["a note"])


class OutputTest(unittest.TestCase):
    def report(self):
        return Report(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC), UTC, [sample_account()])

    def test_summary(self):
        self.assertEqual(summary(sample_account()),
                         "2 pull requests (1 merged, 1 open) · 1 reviewed · 12 commits in 1 repo")

    def test_table(self):
        text = render(self.report(), "table")
        self.assertIn("2026-01-01 to 2026-01-31 (UTC)", text)
        self.assertIn("2026-01-05  merged  o/r#1  Fix [bug] *now*", text)
        self.assertIn("12 commits", text)  # per-day rows rolled up per repo
        self.assertNotIn("10 commits", text)
        self.assertIn("note: a note", text)

    def test_markdown_escapes_titles(self):
        text = render(self.report(), "markdown")
        self.assertIn(r"[o/r#1](https://x/1) — Fix \[bug\] \*now\*", text)
        self.assertEqual(md_escape("a_b|c"), r"a\_b\|c")

    def test_json_and_csv(self):
        data = json.loads(render(self.report(), "json"))
        self.assertEqual(len(data["accounts"][0]["contributions"]), 5)  # raw per-day commit rows
        rows = list(csv.DictReader(io.StringIO(render(self.report(), "csv"))))
        self.assertEqual(rows[0]["ref"], "o/r#1")
        self.assertEqual([r["count"] for r in rows if r["kind"] == "commits"], ["10", "2"])


class FakeProvider:
    def collect(self, since, until):
        return sample_account()


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), provider_factory=lambda account, token, verbose: FakeProvider())
        return code, out.getvalue()

    def test_filters(self):
        code, out = self.run_cli("--github", "--since", "2026-01-01", "-f", "json", "--exclude", "^o/s$",
                                 "--type", "pull_request", "--type", "review")
        self.assertEqual(code, 0)
        kinds = [c["kind"] for c in json.loads(out)["accounts"][0]["contributions"]]
        self.assertEqual(kinds, ["pull_request", "pull_request"])

    def test_bad_regex_is_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main(["--github", "--include", "("])
        self.assertEqual(ctx.exception.code, 2)


class ScopesCliTest(unittest.TestCase):
    def test_scopes_file_filters_rows(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "scopes.toml")
            with open(path, "w") as handle:
                handle.write('[[scope]]\nname = "S"\nhost = "github.com"\nowner = "o"\nrepos = "s"\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                cli.main(["--github", "-f", "json", "--scopes", path],
                         provider_factory=lambda account, token, verbose: FakeProvider())
        refs = [c["ref"] for c in json.loads(out.getvalue())["accounts"][0]["contributions"]]
        self.assertEqual(refs, ["o/s#3"])
