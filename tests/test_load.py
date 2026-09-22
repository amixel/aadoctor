"""Reading the load average (SPEC-006).

The collector takes a path, so none of this needs a real /proc.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor.collectors import load
from aadoctor.collectors.load import LoadSample


class Parsing(unittest.TestCase):
    def test_a_normal_line(self):
        self.assertEqual(
            load.parse_loadavg("0.42 1.23 2.01 2/441 12345"),
            (0.42, 1.23, 2.01),
        )

    def test_high_values(self):
        self.assertEqual(
            load.parse_loadavg("128.75 96.10 40.00 900/9000 1"),
            (128.75, 96.10, 40.00),
        )

    def test_integers_without_decimals(self):
        self.assertEqual(load.parse_loadavg("1 2 3 1/1 1"), (1.0, 2.0, 3.0))

    def test_a_trailing_newline_is_fine(self):
        self.assertEqual(load.parse_loadavg("0.00 0.00 0.00 1/1 1\n"), (0.0, 0.0, 0.0))

    def test_extra_fields_are_ignored(self):
        self.assertIsNotNone(load.parse_loadavg("1.0 1.0 1.0 1/1 1 extra stuff"))

    def test_empty_content(self):
        with self.assertLogs("aadoctor.load", level="WARNING"):
            self.assertIsNone(load.parse_loadavg(""))

    def test_too_few_fields(self):
        with self.assertLogs("aadoctor.load", level="WARNING"):
            self.assertIsNone(load.parse_loadavg("0.42 1.23"))

    def test_non_numeric_content(self):
        with self.assertLogs("aadoctor.load", level="WARNING"):
            self.assertIsNone(load.parse_loadavg("not a load average at all"))

    def test_negative_values_are_rejected(self):
        """A negative load is impossible; better no sample than a wrong one."""
        with self.assertLogs("aadoctor.load", level="WARNING"):
            self.assertIsNone(load.parse_loadavg("-1.0 0.5 0.5 1/1 1"))


class Reading(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "loadavg"

    def tearDown(self):
        self._tmp.cleanup()

    def test_reading_a_file(self):
        self.path.write_text("3.50 2.00 1.00 5/500 99\n", encoding="utf-8")

        self.assertEqual(load.read_loadavg(self.path), (3.5, 2.0, 1.0))

    def test_a_missing_file_is_a_warning_not_a_crash(self):
        with self.assertLogs("aadoctor.load", level="WARNING"):
            self.assertIsNone(load.read_loadavg(self.path))

    def test_an_unreadable_file_is_a_warning_not_a_crash(self):
        self.path.write_text("1.0 1.0 1.0 1/1 1", encoding="utf-8")

        with mock.patch.object(Path, "read_text", side_effect=PermissionError):
            with self.assertLogs("aadoctor.load", level="WARNING"):
                self.assertIsNone(load.read_loadavg(self.path))


class Cpus(unittest.TestCase):
    def test_a_normal_count(self):
        with mock.patch("os.cpu_count", return_value=4):
            self.assertEqual(load.cpu_count(), (4, False))

    def test_none_falls_back_to_one_and_says_so(self):
        """The count divides the load; it can never be zero or None."""
        with mock.patch("os.cpu_count", return_value=None):
            count, assumed = load.cpu_count()

        self.assertEqual(count, 1)
        self.assertTrue(assumed)

    def test_zero_falls_back_too(self):
        with mock.patch("os.cpu_count", return_value=0):
            self.assertEqual(load.cpu_count(), (1, True))


class Samples(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "loadavg"

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_per_cpu(self):
        sample = LoadSample(load1=8.0, load5=4.0, load15=2.0, cpu_count=4)

        self.assertEqual(sample.load_per_cpu, 2.0)

    def test_a_single_cpu(self):
        sample = LoadSample(load1=3.0, load5=1.0, load15=1.0, cpu_count=1)

        self.assertEqual(sample.load_per_cpu, 3.0)

    def test_collecting_a_whole_sample(self):
        self.path.write_text("8.00 4.00 2.00 3/300 42", encoding="utf-8")
        sample = load.collect(self.path, cpus=4)

        self.assertEqual(sample.load1, 8.0)
        self.assertEqual(sample.cpu_count, 4)
        self.assertEqual(sample.load_per_cpu, 2.0)
        self.assertFalse(sample.cpus_assumed)

    def test_collect_returns_none_when_the_load_cannot_be_read(self):
        with self.assertLogs("aadoctor.load", level="WARNING"):
            self.assertIsNone(load.collect(self.path))

    def test_the_serialised_sample_carries_the_derived_value(self):
        sample = LoadSample(load1=11.82, load5=6.91, load15=3.72, cpu_count=4)
        data = sample.as_dict()

        self.assertEqual(data["load1"], 11.82)
        self.assertEqual(data["cpu_count"], 4)
        self.assertEqual(data["load_per_cpu"], 2.955)
        self.assertNotIn("cpus_assumed", data)

    def test_an_assumed_cpu_count_is_visible_in_the_record(self):
        sample = LoadSample(load1=2.0, load5=1.0, load15=1.0, cpu_count=1, cpus_assumed=True)

        self.assertTrue(sample.as_dict()["cpus_assumed"])


if __name__ == "__main__":
    unittest.main()
