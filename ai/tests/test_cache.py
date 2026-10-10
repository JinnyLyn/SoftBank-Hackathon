"""캐시 I/O 오류는 관찰 가능해야 하며 응답 원문이나 경로를 로그에 넣지 않는다."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paved_ai import cache


class CacheIoTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        env = mock.patch.dict(os.environ, {"PAVED_AI_CACHE": "on", "PAVED_AI_CACHE_DIR": directory.name})
        env.start()
        self.addCleanup(env.stop)

    def test_missing_entry_is_normal_but_corruption_is_reported(self):
        with self.assertNoLogs(cache.log, level="WARNING"):
            self.assertIsNone(cache.get("missing"))
        for content in ("not-json", "[]", '{"text": 123}'):
            (self.directory / "bad.json").write_text(content)
            with self.assertLogs(cache.log, level="WARNING"):
                self.assertIsNone(cache.get("bad"))

    def test_read_failure_does_not_log_error_contents(self):
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("fake-private-path")):
            with self.assertLogs(cache.log, level="WARNING") as captured:
                self.assertIsNone(cache.get("key"))
        self.assertNotIn("fake-private-path", " ".join(captured.output))

    def test_failed_replace_cleans_temporary_response(self):
        with mock.patch.object(cache.os, "replace", side_effect=OSError("fake-private-path")):
            with self.assertLogs(cache.log, level="WARNING") as captured:
                cache.put("key", {"text": "fake-private-response"})
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertNotIn("fake-private", " ".join(captured.output))
