import unittest

from tests.helpers import *  # noqa: F401,F403
from git_giveback.http import parse_link_header, retry_wait


class HttpTest(unittest.TestCase):
    def test_parse_link_header(self):
        value = '<https://x/api?page=2>; rel="next", <https://x/api?page=9>; rel="last"'
        self.assertEqual(parse_link_header(value), {"next": "https://x/api?page=2", "last": "https://x/api?page=9"})
        self.assertEqual(parse_link_header(None), {})

    def test_retry_wait(self):
        self.assertEqual(retry_wait(403, {"Retry-After": "30"}, 0), 30)
        self.assertEqual(retry_wait(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1010"}, 0, now=1000), 11)
        self.assertIsNone(retry_wait(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5000"}, 0, now=1000))
        self.assertIsNone(retry_wait(403, {}, 0))  # permission problem, not a rate limit
        self.assertIsNone(retry_wait(404, {}, 0))
        self.assertEqual(retry_wait(502, {}, 2), 4)
