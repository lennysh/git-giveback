import os
import tempfile
import unittest

from tests.helpers import *  # noqa: F401,F403
from git_giveback.config import Account, accounts_from_config, auto_detect, load_config, resolve_token
from git_giveback.models import ProviderError


def no_cli(cmd):
    return None


class ConfigTest(unittest.TestCase):
    def test_accounts_from_config(self):
        accounts = accounts_from_config({
            "github": [{}, {"host": "github.example.com", "token_env": "GHE"}],
            "gitlab": [{"url": "gitlab.example.com/", "username": "jdoe"}],
        })
        self.assertEqual([(a.provider, a.host) for a in accounts], [
            ("github", "github.com"), ("github", "github.example.com"), ("gitlab", "https://gitlab.example.com"),
        ])
        self.assertEqual(accounts[2].username, "jdoe")

    def test_load_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_config(None, {"XDG_CONFIG_HOME": tmp}), {})
            with self.assertRaises(ProviderError):
                load_config(os.path.join(tmp, "missing.toml"))
            path = os.path.join(tmp, "c.toml")
            with open(path, "w") as handle:
                handle.write('[[gitlab]]\nurl = "https://gitlab.com"\n')
            self.assertEqual(load_config(path), {"gitlab": [{"url": "https://gitlab.com"}]})

    def test_resolve_token_order(self):
        gh = Account("github", "github.com")
        self.assertEqual(resolve_token(Account("github", "github.com", token="t"), {}, no_cli), "t")
        self.assertEqual(resolve_token(Account("github", "github.com", token_env="X"), {"X": "x"}, no_cli), "x")
        self.assertEqual(resolve_token(gh, {"GITHUB_TOKEN": "env"}, no_cli), "env")
        self.assertEqual(resolve_token(gh, {}, lambda cmd: "from-gh" if cmd[0] == "gh" else None), "from-gh")
        ghe = Account("github", "github.example.com")
        self.assertIsNone(resolve_token(ghe, {"GITHUB_TOKEN": "public-only"}, no_cli))
        self.assertEqual(resolve_token(ghe, {"GH_ENTERPRISE_TOKEN": "e"}, no_cli), "e")
        seen = []
        resolve_token(Account("gitlab", "https://gitlab.example.com"), {}, lambda cmd: seen.append(cmd))
        self.assertEqual(seen[0][-1], "gitlab.example.com")

    def test_auto_detect(self):
        self.assertEqual(auto_detect({}, no_cli), [])
        found = auto_detect({"GITHUB_TOKEN": "a", "GITLAB_TOKEN": "b", "GITLAB_URL": "gitlab.example.com"}, no_cli)
        self.assertEqual([(a.provider, a.host) for a in found],
                         [("github", "github.com"), ("gitlab", "https://gitlab.example.com")])
