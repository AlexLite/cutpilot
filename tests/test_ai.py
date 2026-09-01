import json
import unittest
from unittest.mock import patch

from app.ai import OpenRouterAdapter


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return json.dumps({"choices": [{"message": {"content": '{"commands":[],"summary":"ok"}'}}]}).encode()


class AIAdapterTests(unittest.TestCase):
    def test_request_uses_compact_metadata_and_bounded_output(self):
        adapter = OpenRouterAdapter(api_key="test", model="test", endpoint="http://127.0.0.1/test")
        with patch("app.ai.urlopen", return_value=_Response()) as urlopen:
            result = adapter.create_plan("clip.mov", {"size_bytes": 123}, "сделать MP4")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        user = json.loads(payload["messages"][1]["content"])
        self.assertEqual(result, {"commands": [], "summary": "ok"})
        self.assertEqual(user, {"n": "clip.mov", "b": 123, "t": "сделать MP4"})
        self.assertEqual(payload["max_tokens"], 160)


if __name__ == "__main__":
    unittest.main()
