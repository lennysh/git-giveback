import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from git_giveback.http import Response  # noqa: E402


class FakeClient:
    """Stands in for HttpClient; ``handler(method, url, params, body)`` returns (data, headers)."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def request(self, method, url, params=None, json_body=None):
        self.calls.append((method, url, params, json_body))
        data, headers = self.handler(method, url, params, json_body)
        return Response(200, headers or {}, json.dumps(data))
