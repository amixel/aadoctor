"""Atomic JSON state (ADR-003)."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor import storage


class StorageCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.target = self.root / "state.json"

    def tearDown(self):
        self._tmp.cleanup()


class Reading(StorageCase):
    def test_a_missing_file_reads_as_none(self):
        self.assertIsNone(storage.read_json(self.target))

    def test_a_round_trip(self):
        storage.write_json(self.target, {"files": {"a": 1}})

        self.assertEqual(storage.read_json(self.target), {"files": {"a": 1}})

    def test_invalid_json_reads_as_none_with_a_warning(self):
        self.target.write_text("{ broken", encoding="utf-8")

        with self.assertLogs("aadoctor.storage", level="WARNING"):
            self.assertIsNone(storage.read_json(self.target))

    def test_invalid_bytes_read_as_none(self):
        self.target.write_bytes(b"\xff\xfe\x00broken")

        with self.assertLogs("aadoctor.storage", level="WARNING"):
            self.assertIsNone(storage.read_json(self.target))

    def test_an_empty_file_reads_as_none(self):
        self.target.write_text("", encoding="utf-8")

        with self.assertLogs("aadoctor.storage", level="WARNING"):
            self.assertIsNone(storage.read_json(self.target))


class Writing(StorageCase):
    def test_the_parent_directory_is_created(self):
        nested = self.root / "deeper" / "state.json"

        self.assertTrue(storage.write_json(nested, {"a": 1}))
        self.assertTrue(nested.exists())

    def test_no_temporary_file_is_left_behind(self):
        storage.write_json(self.target, {"a": 1})

        self.assertEqual([p.name for p in self.root.iterdir()], ["state.json"])

    def test_a_previous_file_is_replaced_not_appended_to(self):
        storage.write_json(self.target, {"a": 1})
        storage.write_json(self.target, {"b": 2})

        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"b": 2})

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_state_is_not_world_readable(self):
        storage.write_json(self.target, {"a": 1})
        mode = stat.S_IMODE(self.target.stat().st_mode)

        self.assertEqual(mode & 0o007, 0, oct(mode))

    def test_a_failed_write_leaves_the_previous_file_intact(self):
        storage.write_json(self.target, {"good": True})

        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertLogs("aadoctor.storage", level="WARNING"):
                self.assertFalse(storage.write_json(self.target, {"bad": True}))

        self.assertEqual(storage.read_json(self.target), {"good": True})
        self.assertEqual([p.name for p in self.root.iterdir()], ["state.json"])

    def test_a_write_failure_is_reported_not_raised(self):
        with mock.patch("os.open", side_effect=PermissionError):
            with self.assertLogs("aadoctor.storage", level="WARNING"):
                self.assertFalse(storage.write_json(self.target, {"a": 1}))


if __name__ == "__main__":
    unittest.main()
