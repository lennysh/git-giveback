import unittest

from tests.helpers import *  # noqa: F401,F403
from git_giveback.scopes import ScopeError, parse_scopes, scope_for

SCOPES = parse_scopes({"scope": [
    {"name": "CoP", "host": "github.com", "owner": "redhat-cop", "exclude": "old-.*"},
    {"name": "SAP", "host": "github.com", "owner": "redhat-sap", "repos": "^sap-(rhsm|monitoring)$",
     "types": ["pull_request", "review"]},
    {"name": "GL", "host": "gitlab.com", "owner": "grp"},
]})


def name(host, repo, kind="pull_request"):
    scope = scope_for(SCOPES, host, repo, kind)
    return scope.name if scope else None


class ScopesTest(unittest.TestCase):
    def test_matching(self):
        self.assertEqual(name("github.com", "redhat-cop/infra.aap_configuration"), "CoP")
        self.assertEqual(name("GitHub.com", "RedHat-CoP/anything"), "CoP")  # case-insensitive
        self.assertIsNone(name("github.com", "redhat-cop/old-thing"))  # excluded
        self.assertIsNone(name("github.com", "redhat-cop-fork/x"))  # owner must match exactly
        self.assertIsNone(name("github.example.com", "redhat-cop/x"))  # host must match
        self.assertEqual(name("github.com", "redhat-sap/sap-rhsm"), "SAP")
        self.assertIsNone(name("github.com", "redhat-sap/sap-rhsm-extra"))  # full match only
        self.assertIsNone(name("github.com", "redhat-sap/sap-rhsm", "commits"))  # type not allowed
        self.assertEqual(name("gitlab.com", "grp/sub/project"), "GL")  # subgroups included

    def test_validation(self):
        for bad in ({}, {"scope": []}, {"scope": [{"name": "x", "host": "h"}]},
                    {"scope": [{"name": "x", "host": "h", "owner": "o", "typo": 1}]},
                    {"scope": [{"name": "x", "host": "h", "owner": "o", "types": ["bogus"]}]},
                    {"scope": [{"name": "x", "host": "h", "owner": "o", "repos": "("}]}):
            with self.assertRaises(ScopeError):
                parse_scopes(bad)
