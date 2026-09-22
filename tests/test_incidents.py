"""Load incident detection (SPEC-006).

The detector's clock is injected and load samples are handed to it directly,
so the whole state machine is tested without a real /proc and without waiting.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor.analyzers import incidents
from aadoctor.analyzers.incidents import IncidentDetector
from aadoctor.analyzers.traffic import TrafficAggregator
from aadoctor.collectors.load import LoadSample
from aadoctor.parsers.nginx_access import AccessEvent
from aadoctor.parsers.nginx_error import ErrorEvent


def sample(per_cpu, cpus=4, load5=None, load15=None):
    """A load sample with the given load per core."""
    load1 = per_cpu * cpus
    return LoadSample(
        load1=round(load1, 4),
        load5=load5 if load5 is not None else round(load1 * 0.6, 4),
        load15=load15 if load15 is not None else round(load1 * 0.3, 4),
        cpu_count=cpus,
    )


class DetectorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.moment = datetime(2026, 9, 22, 18, 31, 40, tzinfo=timezone.utc)
        self.detector = self.build()

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, **kwargs):
        options = dict(
            directory=self.directory,
            trigger_per_cpu=1.0,
            critical_per_cpu=2.0,
            recovery_per_cpu=0.75,
            trigger_polls=2,
            recovery_polls=3,
            clock=lambda: self.moment,
        )
        options.update(kwargs)
        return IncidentDetector(**options)

    def feed(self, *values, capture=None):
        """Feed a sequence of load-per-core values, one poll apart."""
        opened = []
        for value in values:
            result = self.detector.observe(sample(value), capture)
            if result is not None:
                opened.append(result)
            self.moment += timedelta(seconds=10)
        return opened

    def files(self):
        return sorted(path.name for path in self.directory.iterdir())


class Opening(DetectorCase):
    def test_quiet_load_opens_nothing(self):
        self.feed(0.1, 0.3, 0.5, 0.2)

        self.assertIsNone(self.detector.open_incident)
        self.assertEqual(self.files(), [])

    def test_a_single_spike_is_not_an_incident(self):
        """One poll above the line is noise, not an event."""
        self.feed(0.5, 1.4, 0.5, 0.4)

        self.assertIsNone(self.detector.open_incident)
        self.assertEqual(self.files(), [])

    def test_sustained_load_opens_one(self):
        opened = self.feed(0.5, 1.2, 1.4)

        self.assertEqual(len(opened), 1)
        self.assertIsNotNone(self.detector.open_incident)
        self.assertEqual(self.files(), [opened[0].id + ".json"])

    def test_it_opens_on_the_configured_number_of_polls(self):
        self.detector = self.build(trigger_polls=3)
        self.feed(1.5, 1.5)
        self.assertIsNone(self.detector.open_incident)

        self.feed(1.5)
        self.assertIsNotNone(self.detector.open_incident)

    def test_the_count_resets_when_load_drops(self):
        self.feed(1.5, 0.2, 1.5)

        self.assertIsNone(self.detector.open_incident)

    def test_the_opening_sample_is_recorded(self):
        opened = self.feed(1.2, 1.6)[0]

        self.assertEqual(opened.start_load["load1"], 6.4)
        self.assertEqual(opened.start_load["load_per_cpu"], 1.6)
        self.assertEqual(opened.cpu_count, 4)

    def test_startup_with_load_already_high_opens_normally(self):
        """No history is invented; the incident simply starts now."""
        opened = self.feed(3.0, 3.0)

        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].severity, incidents.CRITICAL)


class Continuity(DetectorCase):
    def test_a_continuous_spike_is_one_incident(self):
        """The whole point: not one incident per poll."""
        opened = self.feed(0.5, 1.1, 1.8, 2.7, 2.1, 1.4, 1.2, 1.1)

        self.assertEqual(len(opened), 1)
        self.assertEqual(len(self.files()), 1)

    def test_it_stays_open_between_the_two_thresholds(self):
        self.feed(1.2, 1.5)
        self.feed(0.9, 0.8, 0.85, 0.9)

        self.assertIsNotNone(self.detector.open_incident)

    def test_hysteresis_prevents_flapping(self):
        """1.10 / 0.95 / 1.05 / 0.90 must not open and close repeatedly."""
        opened = self.feed(1.10, 0.95, 1.05, 0.90, 1.10, 0.95)

        self.assertEqual(opened, [])
        self.assertEqual(self.files(), [])

    def test_the_recovery_threshold_is_never_above_the_trigger(self):
        detector = self.build(recovery_per_cpu=5.0, trigger_per_cpu=1.0)

        self.assertLessEqual(detector.recovery_per_cpu, detector.trigger_per_cpu)


class Peaks(DetectorCase):
    def test_the_peak_rises_with_the_load(self):
        opened = self.feed(1.2, 1.5)[0]
        self.feed(2.9)

        self.assertEqual(opened.peak_load["load_per_cpu"], 2.9)

    def test_the_peak_is_kept_when_load_falls_back(self):
        opened = self.feed(1.2, 1.5)[0]
        self.feed(2.9, 1.6, 1.2)

        self.assertEqual(opened.peak_load["load_per_cpu"], 2.9)
        self.assertIsNotNone(self.detector.open_incident)

    def test_severity_rises_to_critical_and_stays(self):
        opened = self.feed(1.2, 1.5)[0]
        self.assertEqual(opened.severity, incidents.HIGH)

        self.feed(2.5)
        self.assertEqual(opened.severity, incidents.CRITICAL)

        self.feed(1.1)
        self.assertEqual(opened.severity, incidents.CRITICAL)

    def test_the_peak_time_is_recorded(self):
        opened = self.feed(1.2, 1.5)[0]
        first_peak = opened.peak_at
        self.feed(2.9)

        self.assertNotEqual(opened.peak_at, first_peak)


class Closing(DetectorCase):
    def test_it_closes_after_sustained_recovery(self):
        opened = self.feed(1.2, 1.5)[0]
        self.feed(0.5, 0.4, 0.3)

        self.assertIsNone(self.detector.open_incident)
        self.assertEqual(opened.status, incidents.CLOSED)
        self.assertIsNotNone(opened.ended_at)

    def test_it_does_not_close_on_one_quiet_poll(self):
        self.feed(1.2, 1.5)
        self.feed(0.2, 1.5, 0.2)

        self.assertIsNotNone(self.detector.open_incident)

    def test_the_duration_is_derived_from_the_timestamps(self):
        opened = self.feed(1.2, 1.5)[0]
        self.feed(0.1, 0.1, 0.1)

        # Opened on the second poll, closed on the fifth, ten seconds apart.
        self.assertAlmostEqual(opened.duration_seconds(), 30.0, places=1)

    def test_a_second_independent_incident_gets_its_own_file(self):
        self.feed(1.2, 1.5)
        self.feed(0.1, 0.1, 0.1)
        second = self.feed(1.2, 1.5)

        self.assertEqual(len(second), 1)
        self.assertEqual(len(self.files()), 2)

    def test_ids_do_not_collide_within_one_second(self):
        frozen = self.moment
        self.detector = self.build(clock=lambda: frozen)

        self.detector.observe(sample(1.5))
        self.detector.observe(sample(1.5))
        first = self.detector.open_incident
        for _ in range(3):
            self.detector.observe(sample(0.1))
        self.detector.observe(sample(1.5))
        self.detector.observe(sample(1.5))
        second = self.detector.open_incident

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(len(self.files()), 2)


class Snapshots(DetectorCase):
    """The traffic frozen with an incident, and when it is refreshed."""

    def capture_factory(self, state):
        return lambda: {"300": dict(state)}

    def test_the_opening_snapshot_is_stored(self):
        state = {"total_requests": 100}
        opened = self.feed(1.2, 1.5, capture=self.capture_factory(state))[0]

        self.assertEqual(opened.traffic_at_start["300"]["total_requests"], 100)

    def test_the_peak_snapshot_replaces_the_opening_one(self):
        """The traffic responsible is often clearer at the peak (SPEC-006)."""
        state = {"top_site_share": 0.55, "wp_cron": 0}
        capture = self.capture_factory(state)

        opened = self.feed(1.1, 1.2, capture=capture)[0]
        self.assertEqual(opened.traffic_at_peak["300"]["top_site_share"], 0.55)

        state["top_site_share"] = 0.84
        state["wp_cron"] = 5000
        self.feed(2.5, capture=capture)

        self.assertEqual(opened.traffic_at_peak["300"]["top_site_share"], 0.84)
        self.assertEqual(opened.traffic_at_peak["300"]["wp_cron"], 5000)
        # The opening snapshot is not rewritten: it is what it was.
        self.assertEqual(opened.traffic_at_start["300"]["top_site_share"], 0.55)

    def test_no_snapshot_is_taken_while_nothing_happens(self):
        calls = []

        def capture():
            calls.append(1)
            return {}

        self.feed(0.2, 0.3, 0.4, 0.5, capture=capture)

        self.assertEqual(calls, [])

    def test_an_incident_without_traffic_data_is_still_recorded(self):
        opened = self.feed(1.2, 1.5, capture=lambda: None)[0]

        self.assertIsNone(opened.traffic_at_start)
        self.assertEqual(opened.status, incidents.OPEN)


class Persistence(DetectorCase):
    def read(self, incident_id):
        path = self.directory / f"{incident_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_file_is_written_when_the_incident_opens(self):
        opened = self.feed(1.2, 1.5)[0]
        stored = self.read(opened.id)

        self.assertEqual(stored["status"], "open")
        self.assertEqual(stored["schema_version"], incidents.SCHEMA_VERSION)
        self.assertEqual(stored["system"]["cpu_count"], 4)

    def test_the_file_is_updated_on_a_new_peak(self):
        opened = self.feed(1.2, 1.5)[0]
        self.feed(2.9)

        self.assertEqual(self.read(opened.id)["system"]["peak"]["load_per_cpu"], 2.9)

    def test_the_file_records_the_close(self):
        opened = self.feed(1.2, 1.5)[0]
        self.feed(2.9)
        self.feed(0.1, 0.1, 0.1)
        stored = self.read(opened.id)

        self.assertEqual(stored["status"], "closed")
        self.assertIsNotNone(stored["ended_at"])
        self.assertEqual(stored["system"]["peak"]["load_per_cpu"], 2.9)
        self.assertIn("duration_seconds", stored)

    def test_the_record_carries_no_verdict(self):
        """SPEC-006 records what was measured. SPEC-007 concludes."""
        opened = self.feed(1.2, 1.5)[0]
        stored = json.dumps(self.read(opened.id)).lower()

        for word in ("root_cause", "responsible", "confidence", "attack", "finding",
                     "culprit", "probable"):
            self.assertNotIn(word, stored)

    def test_a_write_failure_does_not_raise(self):
        with mock.patch("aadoctor.analyzers.incidents.write_json", return_value=False):
            with self.assertLogs("aadoctor.incidents", level="WARNING"):
                opened = self.feed(1.2, 1.5)

        self.assertEqual(len(opened), 1)

    def test_an_incident_round_trips(self):
        opened = self.feed(1.2, 1.5)[0]
        self.feed(0.1, 0.1, 0.1)

        restored = incidents.load_incident(opened.id, self.directory)

        self.assertEqual(restored.id, opened.id)
        self.assertEqual(restored.status, incidents.CLOSED)
        self.assertEqual(restored.peak_load_per_cpu, opened.peak_load_per_cpu)


class Recovery(DetectorCase):
    def test_an_incident_left_open_is_marked_interrupted(self):
        opened = self.feed(1.2, 1.5)[0]

        fresh = self.build()
        recovered = fresh.recover()

        self.assertEqual(recovered, [opened.id])
        restored = incidents.load_incident(opened.id, self.directory)
        self.assertEqual(restored.status, incidents.INTERRUPTED)
        self.assertIsNone(restored.ended_at)

    def test_closed_incidents_are_left_alone(self):
        opened = self.feed(1.2, 1.5)[0]
        self.feed(0.1, 0.1, 0.1)

        self.assertEqual(self.build().recover(), [])
        self.assertEqual(
            incidents.load_incident(opened.id, self.directory).status, incidents.CLOSED
        )


class Reading(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, payload):
        path = self.directory / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def incident(self, incident_id):
        return {
            "schema_version": 1,
            "id": incident_id,
            "status": "closed",
            "started_at": "2026-09-22T18:31:40+00:00",
            "ended_at": "2026-09-22T18:34:10+00:00",
            "system": {"cpu_count": 4, "peak": {"load_per_cpu": 2.95}},
        }

    def test_an_empty_directory(self):
        self.assertEqual(incidents.load_incidents(self.directory), [])

    def test_a_missing_directory(self):
        self.assertEqual(incidents.load_incidents(self.directory / "nope"), [])

    def test_newest_first(self):
        for stamp in ("2026-09-22T16-12-20", "2026-09-22T18-31-40", "2026-09-21T10-00-00"):
            self.write(f"{stamp}.json", self.incident(stamp))

        found = [item.id for item in incidents.load_incidents(self.directory)]

        self.assertEqual(found[0], "2026-09-22T18-31-40")
        self.assertEqual(found[-1], "2026-09-21T10-00-00")

    def test_the_limit_is_respected(self):
        for index in range(10):
            stamp = f"2026-09-22T18-31-{index:02d}"
            self.write(f"{stamp}.json", self.incident(stamp))

        self.assertEqual(len(incidents.load_incidents(self.directory, limit=3)), 3)

    def test_files_that_are_not_incidents_are_ignored(self):
        self.write("2026-09-22T18-31-40.json", self.incident("2026-09-22T18-31-40"))
        self.write("notes.json", {"id": "x"})
        (self.directory / "state.json").write_text("{}", encoding="utf-8")

        self.assertEqual(len(incidents.load_incidents(self.directory)), 1)

    def test_an_unreadable_incident_is_skipped_not_fatal(self):
        self.write("2026-09-22T18-31-40.json", self.incident("2026-09-22T18-31-40"))
        (self.directory / "2026-09-22T18-31-41.json").write_text("{ broken", encoding="utf-8")

        with self.assertLogs("aadoctor", level="WARNING"):
            found = incidents.load_incidents(self.directory)

        self.assertEqual(len(found), 1)

    def test_loading_one_by_id(self):
        self.write("2026-09-22T18-31-40.json", self.incident("2026-09-22T18-31-40"))

        found = incidents.load_incident("2026-09-22T18-31-40", self.directory)
        self.assertEqual(found.peak_load_per_cpu, 2.95)

    def test_an_unknown_id(self):
        self.assertIsNone(incidents.load_incident("2026-01-01T00-00-00", self.directory))

    def test_an_id_that_is_not_an_id_cannot_reach_the_filesystem(self):
        """The id comes from the command line; it may not become a path."""
        for hostile in ("../../etc/passwd", "state", "..", "a/b"):
            self.assertIsNone(incidents.load_incident(hostile, self.directory))


class Retention(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, age_days=0):
        path = self.directory / name
        path.write_text(json.dumps({"id": path.stem}), encoding="utf-8")
        if age_days:
            old = time.time() - age_days * 86400
            os.utime(str(path), (old, old))
        return path

    def test_old_incidents_are_removed(self):
        old = self.write("2026-01-01T00-00-00.json", age_days=40)
        recent = self.write("2026-09-22T18-31-40.json", age_days=1)

        removed = incidents.purge_old(self.directory, retention_days=30)

        self.assertEqual(removed, ["2026-01-01T00-00-00"])
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_files_that_are_not_ours_are_never_touched(self):
        """Only names that look like an incident id are even considered."""
        stranger = self.write("important-backup.json", age_days=900)
        state = self.write("state.json", age_days=900)

        incidents.purge_old(self.directory, retention_days=30)

        self.assertTrue(stranger.exists())
        self.assertTrue(state.exists())

    def test_retention_of_zero_removes_nothing(self):
        old = self.write("2026-01-01T00-00-00.json", age_days=900)

        self.assertEqual(incidents.purge_old(self.directory, retention_days=0), [])
        self.assertTrue(old.exists())

    def test_a_missing_directory_is_not_an_error(self):
        self.assertEqual(incidents.purge_old(self.directory / "nope", 30), [])


class RealisticScenario(DetectorCase):
    """The scenario this whole SPEC exists for.

    Load rises over a burst of traffic, and the incident must preserve what the
    logs showed at the peak - without claiming any of it caused anything.
    """

    def setUp(self):
        super().setUp()
        self.aggregator = TrafficAggregator()

        # Ordinary traffic across three sites.
        self._requests("site-b", "/", "9.9.9.1", 200, 1000)
        self._requests("site-c", "/blog", "9.9.8.1", 200, 500)
        self._requests("site-a", "/", "9.9.7.1", 200, 3000)

        capture = self._capture()

        # Load climbs while one path takes off.
        self.opened = self.feed(0.5, 0.7, capture=capture)
        self.assertEqual(self.opened, [])

        self.opened = self.feed(1.2, 1.8, capture=capture)
        self.assertEqual(len(self.opened), 1)

        self._requests("site-a", "/wp-cron.php", "185.1.2.3", 200, 4500)
        self._requests("site-a", "/wp-cron.php", "177.9.9.9", 200, 380)
        self._requests("site-a", "/wp-cron.php", "185.1.2.3", 502, 120)
        for _ in range(40):
            self.aggregator.add(
                ErrorEvent(level="error", kind="upstream_timeout",
                           message="upstream timed out", site="site-a")
            )

        # The peak arrives after the burst.
        self.feed(2.9, 2.4, capture=capture)
        self.feed(1.3, 0.6, 0.5, 0.4, capture=capture)

        self.incident = self.opened[0]

    def _requests(self, site, path, ip, status, count):
        for _ in range(count):
            self.aggregator.add(
                AccessEvent(remote_addr=ip, method="GET", path=path,
                            status=status, site=site)
            )

    def _capture(self):
        return lambda: {
            "300": self.aggregator.snapshot(window_seconds=300).as_dict()
        }

    def test_exactly_one_incident_was_recorded(self):
        self.assertEqual(len(self.files()), 1)

    def test_it_closed(self):
        self.assertEqual(self.incident.status, incidents.CLOSED)
        self.assertIsNone(self.detector.open_incident)

    def test_the_peak_load_is_preserved(self):
        self.assertEqual(self.incident.peak_load["load_per_cpu"], 2.9)
        self.assertEqual(self.incident.peak_load["load1"], 11.6)
        self.assertEqual(self.incident.severity, incidents.CRITICAL)

    def test_the_peak_snapshot_shows_the_burst(self):
        window = self.incident.traffic_at_peak["300"]

        self.assertEqual(window["total_requests"], 9500)
        self.assertEqual(window["sites"][0]["key"], "site-a")
        self.assertEqual(window["sites"][0]["count"], 8000)

    def test_the_peak_snapshot_shows_the_path_and_the_address(self):
        site = [
            entry for entry in self.incident.traffic_at_peak["300"]["per_site"]
            if entry["name"] == "site-a"
        ][0]

        self.assertEqual(site["paths"][0]["key"], "/wp-cron.php")
        self.assertEqual(site["paths"][0]["count"], 5000)
        self.assertEqual(site["ips"][0]["key"], "185.1.2.3")
        self.assertEqual(site["ips"][0]["count"], 4620)

    def test_the_peak_snapshot_shows_the_errors(self):
        site = [
            entry for entry in self.incident.traffic_at_peak["300"]["per_site"]
            if entry["name"] == "site-a"
        ][0]

        self.assertEqual(site["statuses"]["502"], 120)
        self.assertEqual(site["error_kinds"]["upstream_timeout"], 40)

    def test_the_opening_snapshot_predates_the_burst(self):
        """Which is exactly why the peak snapshot had to be taken as well."""
        opening = self.incident.traffic_at_start["300"]

        self.assertEqual(opening["total_requests"], 4500)

    def test_the_record_still_names_no_cause(self):
        stored = json.dumps(self.incident.as_dict()).lower()

        for word in ("cause", "responsible", "culprit", "attack", "confidence",
                     "finding", "probable"):
            self.assertNotIn(word, stored)

    def test_the_incident_file_stays_small(self):
        path = self.directory / f"{self.incident.id}.json"
        size = path.stat().st_size

        self.assertLess(size, 200 * 1024, f"incident file is {size} bytes")

    def test_the_snapshot_says_how_much_data_it_covers(self):
        window = self.incident.traffic_at_peak["300"]

        self.assertIn("coverage_seconds", window)
        self.assertLessEqual(window["coverage_seconds"], 300)


if __name__ == "__main__":
    unittest.main()
