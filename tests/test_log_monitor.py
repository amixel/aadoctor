"""Incremental log monitoring (SPEC-003).

Every test works in a temporary directory. Nothing here touches /www, needs
aaPanel, or needs root.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor import discovery
from aadoctor.collectors import logs as collector
from aadoctor.collectors.logs import ACCESS, ERROR, LogMonitor


class MonitorCase(unittest.TestCase):
    """A temporary directory, a monitor, and helpers to drive real files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state_path = self.root / "state.json"
        self.logs = self.root / "logs"
        self.logs.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ----------------------------------------------------------

    def monitor(self, **kwargs):
        return LogMonitor(state_path=self.state_path, **kwargs)

    def log_path(self, name="site.log"):
        return self.logs / name

    def write(self, path, text, mode="a"):
        with open(str(path), mode, encoding="utf-8") as handle:
            handle.write(text)

    def write_bytes(self, path, payload, mode="ab"):
        with open(str(path), mode) as handle:
            handle.write(payload)

    def watch(self, monitor, *paths, log_type=ACCESS, site="example.com"):
        """Point the monitor at files directly, without going through aaPanel."""
        monitor._watched = {
            str(path): collector.WatchedLog(Path(path), log_type, (site,))
            for path in paths
        }

    def poll(self, monitor):
        """Poll and return (lines, stats)."""
        collected = []
        stats = monitor.poll(collected.append)
        return [event.line for event in collected], stats


class FirstObservation(MonitorCase):
    def test_existing_content_is_never_read(self):
        path = self.log_path()
        self.write(path, "historical one\nhistorical two\n", mode="w")

        monitor = self.monitor()
        self.watch(monitor, path)

        lines, stats = self.poll(monitor)

        self.assertEqual(lines, [])
        self.assertEqual(stats.bytes_read, 0)
        self.assertEqual(stats.first_seen, 1)
        self.assertEqual(monitor.offset_of(path), path.stat().st_size)

    def test_only_content_appended_afterwards_is_emitted(self):
        path = self.log_path()
        self.write(path, "old\n", mode="w")

        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "NEW-LINE\n")
        lines, _ = self.poll(monitor)

        self.assertEqual(lines, ["NEW-LINE"])

    def test_a_large_file_costs_no_reads_on_first_sight(self):
        """The whole point of SPEC-003: history is not ours to read."""
        path = self.log_path()
        size = 8 * 1024 * 1024
        with open(str(path), "wb") as handle:
            handle.seek(size - 1)
            handle.write(b"\0")

        monitor = self.monitor()
        self.watch(monitor, path)
        lines, stats = self.poll(monitor)

        self.assertEqual(lines, [])
        self.assertEqual(stats.bytes_read, 0)
        self.assertEqual(monitor.offset_of(path), size)

        self.write(path, "after the big file\n")
        lines, stats = self.poll(monitor)

        self.assertEqual(lines, ["after the big file"])
        self.assertLess(stats.bytes_read, 100)


class Appending(MonitorCase):
    def test_one_line(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "first\n")

        self.assertEqual(self.poll(monitor)[0], ["first"])

    def test_several_lines_in_order(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "a\nb\nc\n")

        self.assertEqual(self.poll(monitor)[0], ["a", "b", "c"])

    def test_no_new_content_reads_nothing(self):
        path = self.log_path()
        self.write(path, "x\n", mode="w")
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        lines, stats = self.poll(monitor)

        self.assertEqual(lines, [])
        self.assertEqual(stats.bytes_read, 0)
        self.assertFalse(stats.changed)

    def test_the_offset_advances_by_the_bytes_consumed(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "hello\n")
        self.poll(monitor)

        self.assertEqual(monitor.offset_of(path), len(b"hello\n"))

    def test_blank_lines_are_not_emitted(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "a\n\n\nb\n")

        self.assertEqual(self.poll(monitor)[0], ["a", "b"])


class PartialLines(MonitorCase):
    def test_a_line_without_a_newline_is_held_back(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "abc def")
        lines, _ = self.poll(monitor)

        self.assertEqual(lines, [])
        self.assertEqual(monitor.offset_of(path), 0)

    def test_the_fragment_is_completed_on_a_later_poll(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "abc def")
        self.poll(monitor)
        self.write(path, "ghi\n")

        self.assertEqual(self.poll(monitor)[0], ["abc defghi"])

    def test_a_fragment_survives_a_restart(self):
        """Nothing partial is persisted: the offset simply stayed put."""
        path = self.log_path()
        path.touch()
        first = self.monitor()
        self.watch(first, path)
        self.poll(first)
        self.write(path, "half a line")
        self.poll(first)
        first.save()

        second = self.monitor()
        self.watch(second, path)
        self.write(path, " and the rest\n")

        self.assertEqual(self.poll(second)[0], ["half a line and the rest"])

    def test_a_line_longer_than_the_limit_is_emitted_truncated(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor(max_line_bytes=1024, chunk_bytes=1024)
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "x" * 5000)
        with self.assertLogs("aadoctor.logs", level="WARNING"):
            lines, _ = self.poll(monitor)

        self.assertTrue(lines)
        self.assertGreater(monitor.offset_of(path), 0)

    def test_carriage_returns_are_stripped(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write_bytes(path, b"windows line\r\n")

        self.assertEqual(self.poll(monitor)[0], ["windows line"])


class Restart(MonitorCase):
    def test_offsets_survive_a_restart(self):
        path = self.log_path()
        path.touch()

        first = self.monitor()
        self.watch(first, path)
        self.poll(first)
        self.write(path, "one\n")
        self.poll(first)
        self.assertTrue(first.save())

        second = self.monitor()
        self.watch(second, path)
        self.write(path, "two\n")

        # 'one' was already consumed; only 'two' is new.
        self.assertEqual(self.poll(second)[0], ["two"])

    def test_state_is_written_to_disk(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)
        monitor.save()

        self.assertTrue(self.state_path.exists())
        self.assertIn("files", self.state_path.read_text(encoding="utf-8"))

    def test_state_holds_metadata_not_log_content(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)
        self.write(path, "secret request line\n")
        self.poll(monitor)
        monitor.save()

        stored = self.state_path.read_text(encoding="utf-8")
        self.assertNotIn("secret request line", stored)
        self.assertIn("offset", stored)

    def test_saving_is_skipped_when_nothing_moved(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)
        self.assertTrue(monitor.save())

        self.poll(monitor)
        self.assertFalse(monitor.save())


class CorruptState(MonitorCase):
    def _restart_with_state(self, text):
        path = self.log_path()
        self.write(path, "history\n", mode="w")
        self.state_path.write_text(text, encoding="utf-8")

        monitor = self.monitor()
        self.watch(monitor, path)
        return path, monitor

    def test_invalid_json_starts_at_the_end_not_at_byte_zero(self):
        with self.assertLogs("aadoctor.storage", level="WARNING"):
            path, monitor = self._restart_with_state("{not json")

        lines, stats = self.poll(monitor)

        self.assertEqual(lines, [])
        self.assertEqual(stats.bytes_read, 0)
        self.assertEqual(monitor.offset_of(path), path.stat().st_size)

    def test_unexpected_shape_is_ignored(self):
        with self.assertLogs("aadoctor.logs", level="WARNING"):
            path, monitor = self._restart_with_state('["not", "a", "mapping"]')

        self.assertEqual(self.poll(monitor)[0], [])
        self.assertEqual(monitor.offset_of(path), path.stat().st_size)

    def test_a_malformed_entry_is_dropped_and_the_rest_kept(self):
        good = self.log_path("good.log")
        bad = self.log_path("bad.log")
        self.write(good, "g\n", mode="w")
        self.write(bad, "b\n", mode="w")
        payload = (
            '{"version": 1, "files": {'
            '"%s": {"device": 1, "inode": 2, "offset": 0},'
            '"%s": {"inode": "nonsense"}}}' % (good.as_posix(), bad.as_posix())
        )
        self.state_path.write_text(payload, encoding="utf-8")

        with self.assertLogs("aadoctor.logs", level="WARNING"):
            monitor = self.monitor()

        self.assertIsNotNone(monitor.offset_of(good))
        self.assertIsNone(monitor.offset_of(bad))

    def test_a_negative_offset_is_rejected(self):
        state = collector.FileState.from_dict({"device": 1, "inode": 2, "offset": -5})
        self.assertIsNone(state)


class Rotation(MonitorCase):
    def test_a_rotated_file_is_read_from_the_beginning(self):
        """A file we were already following: its replacement is all new."""
        path = self.log_path()
        self.write(path, "before\n", mode="w")

        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)
        self.write(path, "still the old file\n")
        self.assertEqual(self.poll(monitor)[0], ["still the old file"])

        os.rename(str(path), str(self.log_path("site.log.1")))
        self.write(path, "first line of the new file\n", mode="w")

        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, stats = self.poll(monitor)

        self.assertEqual(lines, ["first line of the new file"])
        self.assertEqual(stats.rotations, 1)

    def test_rotation_differs_from_a_first_observation(self):
        """The distinction that matters: known file rotated -> read from 0;
        unknown file -> start at the end."""
        rotated = self.log_path("known.log")
        self.write(rotated, "old\n", mode="w")
        monitor = self.monitor()
        self.watch(monitor, rotated)
        self.poll(monitor)

        os.rename(str(rotated), str(self.log_path("known.log.1")))
        self.write(rotated, "line one\nline two\n", mode="w")
        rotated_lines, _ = self.poll(monitor)

        fresh = self.log_path("fresh.log")
        self.write(fresh, "line one\nline two\n", mode="w")
        self.watch(monitor, rotated, fresh)
        fresh_lines, _ = self.poll(monitor)

        self.assertEqual(rotated_lines, ["line one", "line two"])
        self.assertEqual(fresh_lines, [])

    def test_the_inode_is_what_detects_rotation_not_the_size(self):
        path = self.log_path()
        self.write(path, "aaaa\n", mode="w")
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)
        before = monitor.offset_of(path)

        os.rename(str(path), str(self.log_path("rotated.log")))
        # The replacement is larger than the old offset, so only the inode
        # reveals that this is a different file.
        self.write(path, "bbbbbbbbbbbbbbb\n", mode="w")
        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, stats = self.poll(monitor)

        self.assertEqual(lines, ["bbbbbbbbbbbbbbb"])
        self.assertEqual(stats.rotations, 1)
        self.assertNotEqual(monitor.offset_of(path), before)

    def test_a_deleted_and_recreated_file_is_detected(self):
        """The realistic shape: a fresh log is smaller than the old one.

        The filesystem may hand the new file the same inode number, so the
        size check is what catches this, not the inode.
        """
        path = self.log_path()
        self.write(path, "a much longer original line\n", mode="w")
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        os.unlink(str(path))
        self.write(path, "new\n", mode="w")

        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, stats = self.poll(monitor)

        self.assertEqual(lines, ["new"])
        self.assertEqual(stats.rotations + stats.truncations, 1)

    def test_a_shrinking_file_is_caught_even_when_we_were_behind(self):
        """The last observed size matters, not just our offset."""
        path = self.log_path()
        path.touch()
        monitor = self.monitor(max_bytes_per_poll=1024, chunk_bytes=1024)
        self.watch(monitor, path)
        self.poll(monitor)

        # Grow well past what one poll will read, so the offset stays low.
        self.write(path, ("x" * 99 + "\n") * 40)
        self.poll(monitor)
        self.assertLess(monitor.offset_of(path), 4000)

        with open(str(path), "r+") as handle:
            handle.truncate(2000)

        with self.assertLogs("aadoctor.logs", level="INFO") as captured:
            _, stats = self.poll(monitor)

        self.assertEqual(stats.truncations, 1)
        self.assertTrue(any("truncation" in r.getMessage() for r in captured.records))

    def test_inode_reuse_with_a_larger_replacement_does_not_crash(self):
        """A known gap, documented in SPEC-003.

        If the filesystem reuses the inode *and* the replacement is larger than
        the file it replaced, nothing distinguishes it from an append. The
        monitor must stay healthy and recover on the next rotation; it cannot
        detect this without fingerprinting file content.
        """
        path = self.log_path()
        self.write(path, "short\n", mode="w")
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        os.unlink(str(path))
        self.write(path, "a considerably longer replacement line\n", mode="w")
        self.poll(monitor)

        # Whatever it made of that, it keeps working afterwards.
        self.write(path, "later line\n")
        self.assertIn("later line", self.poll(monitor)[0])


class Truncation(MonitorCase):
    def test_truncation_restarts_at_the_beginning(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write(path, "plenty of content here\n")
        self.assertEqual(self.poll(monitor)[0], ["plenty of content here"])

        # copytruncate keeps the inode and resets the size.
        with open(str(path), "r+") as handle:
            handle.truncate(0)
        self.write(path, "after truncation\n")

        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, stats = self.poll(monitor)

        self.assertEqual(lines, ["after truncation"])
        self.assertEqual(stats.truncations, 1)
        self.assertEqual(stats.rotations, 0)

    def test_truncation_is_not_confused_with_a_first_observation(self):
        path = self.log_path()
        self.write(path, "one\ntwo\n", mode="w")
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        with open(str(path), "r+") as handle:
            handle.truncate(0)
        self.write(path, "fresh\n")

        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, _ = self.poll(monitor)

        # A first observation would have emitted nothing.
        self.assertEqual(lines, ["fresh"])


class MissingFiles(MonitorCase):
    def test_a_configured_log_that_does_not_exist_is_not_an_error(self):
        path = self.log_path("never-written.log")
        monitor = self.monitor()
        self.watch(monitor, path)

        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, stats = self.poll(monitor)

        self.assertEqual(lines, [])
        self.assertEqual(stats.unavailable, 1)

    def test_it_is_reported_once_not_every_poll(self):
        path = self.log_path("never-written.log")
        monitor = self.monitor()
        self.watch(monitor, path)

        with self.assertLogs("aadoctor.logs", level="INFO") as captured:
            self.poll(monitor)
            self.poll(monitor)
            self.poll(monitor)

        self.assertEqual(len(captured.records), 1)

    def test_a_log_that_appears_later_starts_at_its_end(self):
        path = self.log_path("late.log")
        monitor = self.monitor()
        self.watch(monitor, path)
        with self.assertLogs("aadoctor.logs", level="INFO"):
            self.poll(monitor)

        self.write(path, "history written before we saw it\n", mode="w")
        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, _ = self.poll(monitor)

        self.assertEqual(lines, [])

        self.write(path, "genuinely new\n")
        self.assertEqual(self.poll(monitor)[0], ["genuinely new"])

    def test_a_deleted_file_does_not_stop_the_others(self):
        alive = self.log_path("alive.log")
        doomed = self.log_path("doomed.log")
        alive.touch()
        doomed.touch()
        monitor = self.monitor()
        self.watch(monitor, alive, doomed)
        self.poll(monitor)

        os.unlink(str(doomed))
        self.write(alive, "still working\n")

        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, stats = self.poll(monitor)

        self.assertEqual(lines, ["still working"])
        self.assertEqual(stats.unavailable, 1)

    def test_a_recreated_file_is_picked_up_again(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        os.unlink(str(path))
        with self.assertLogs("aadoctor.logs", level="INFO"):
            self.poll(monitor)

        self.write(path, "back again\n", mode="w")
        with self.assertLogs("aadoctor.logs", level="INFO"):
            lines, _ = self.poll(monitor)

        self.assertEqual(lines, ["back again"])


class ReadFailures(MonitorCase):
    def test_permission_denied_does_not_kill_the_monitor(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)

        with mock.patch("os.stat", side_effect=PermissionError):
            with self.assertLogs("aadoctor.logs", level="INFO"):
                lines, stats = self.poll(monitor)

        self.assertEqual(lines, [])
        self.assertEqual(stats.unavailable, 1)

    def test_it_recovers_when_the_file_is_readable_again(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)

        with mock.patch("os.stat", side_effect=PermissionError):
            with self.assertLogs("aadoctor.logs", level="INFO"):
                self.poll(monitor)

        with self.assertLogs("aadoctor.logs", level="INFO") as captured:
            self.poll(monitor)

        self.assertTrue(any("readable again" in r.getMessage() for r in captured.records))

    def test_an_unexpected_error_is_contained(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)

        with mock.patch.object(monitor, "_stat", side_effect=RuntimeError("boom")):
            with self.assertLogs("aadoctor.logs", level="WARNING"):
                lines, _ = self.poll(monitor)

        self.assertEqual(lines, [])


class Encoding(MonitorCase):
    def test_invalid_utf8_does_not_raise(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        self.write_bytes(path, b"GET /caf\xff HTTP/1.1\n")
        lines, _ = self.poll(monitor)

        self.assertEqual(len(lines), 1)
        self.assertIn("GET /caf", lines[0])

    def test_offsets_are_byte_based_not_character_based(self):
        """A multi-byte line must not desynchronise the offset."""
        path = self.log_path()
        path.touch()
        monitor = self.monitor()
        self.watch(monitor, path)
        self.poll(monitor)

        payload = "GET /café-üñ HTTP/1.1\n".encode("utf-8")
        self.write_bytes(path, payload)
        lines, _ = self.poll(monitor)

        self.assertEqual(monitor.offset_of(path), len(payload))
        self.assertGreater(len(payload), len(lines[0]))

        self.write(path, "next\n")
        self.assertEqual(self.poll(monitor)[0], ["next"])


class LargeAppends(MonitorCase):
    def test_a_big_append_is_read_in_bounded_chunks(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor(chunk_bytes=1024, max_bytes_per_poll=4096)
        self.watch(monitor, path)
        self.poll(monitor)

        line = "x" * 99 + "\n"
        self.write(path, line * 200)  # 20000 bytes

        lines, stats = self.poll(monitor)

        self.assertLessEqual(stats.bytes_read, 4096)
        self.assertEqual(stats.behind, [path])
        self.assertTrue(lines)

    def test_the_rest_arrives_on_later_polls(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor(chunk_bytes=1024, max_bytes_per_poll=4096)
        self.watch(monitor, path)
        self.poll(monitor)

        line = "y" * 99 + "\n"
        self.write(path, line * 200)

        seen = 0
        for _ in range(20):
            lines, _ = self.poll(monitor)
            seen += len(lines)
            if seen == 200:
                break

        self.assertEqual(seen, 200)
        self.assertEqual(monitor.offset_of(path), path.stat().st_size)

    def test_lines_spanning_a_chunk_boundary_stay_intact(self):
        path = self.log_path()
        path.touch()
        monitor = self.monitor(chunk_bytes=64, max_bytes_per_poll=65536)
        self.watch(monitor, path)
        self.poll(monitor)

        long_line = "z" * 500
        self.write(path, long_line + "\n")

        lines, _ = self.poll(monitor)

        self.assertEqual(lines, [long_line])


class SeveralFiles(MonitorCase):
    def test_access_and_error_logs_are_distinguished(self):
        access = self.log_path("site.log")
        error = self.log_path("site.error.log")
        access.touch()
        error.touch()

        monitor = self.monitor()
        monitor._watched = {
            str(access): collector.WatchedLog(access, ACCESS, ("example.com",)),
            str(error): collector.WatchedLog(error, ERROR, ("example.com",)),
        }
        monitor.poll()

        self.write(access, "a request\n")
        self.write(error, "an error\n")

        collected = []
        monitor.poll(collected.append)
        by_type = {event.log_type: event for event in collected}

        self.assertEqual(by_type[ACCESS].line, "a request")
        self.assertEqual(by_type[ERROR].line, "an error")
        self.assertEqual(by_type[ACCESS].site, "example.com")

    def test_files_are_polled_in_a_stable_order(self):
        names = ["c.log", "a.log", "b.log"]
        for name in names:
            self.log_path(name).touch()

        monitor = self.monitor()
        self.watch(monitor, *[self.log_path(name) for name in names])
        monitor.poll()

        for name in names:
            self.write(self.log_path(name), f"line from {name}\n")

        first = [event.line for event in _collect(monitor)]

        for name in names:
            self.write(self.log_path(name), f"more from {name}\n")
        second = [event.line for event in _collect(monitor)]

        self.assertEqual(first, [f"line from {name}" for name in sorted(names)])
        self.assertEqual(second, [f"more from {name}" for name in sorted(names)])


class WatchSet(MonitorCase):
    def _result(self, *sites):
        return discovery.DiscoveryResult(vhost_dir=self.root, sites=list(sites))

    def _site(self, name, access=None, error=None):
        site = discovery.SiteConfig(name=name, server_names=[name], config_path=self.root / f"{name}.conf")
        if access is not None:
            site.access = discovery.LogTarget(state=discovery.LOG_CONFIGURED, path=access)
        if error is not None:
            site.error = discovery.LogTarget(state=discovery.LOG_CONFIGURED, path=error)
        return site

    def test_only_configured_logs_are_watched(self):
        monitor = self.monitor()
        site = self._site("a.com", access=self.log_path("a.log"))
        site.error = discovery.LogTarget(state=discovery.LOG_DISABLED)

        added, removed = monitor.update_sources(self._result(site))

        self.assertEqual(monitor.watched_count, 1)
        self.assertEqual(added, [str(self.log_path("a.log"))])
        self.assertEqual(removed, [])

    def test_disabled_absent_and_unresolved_logs_are_skipped(self):
        monitor = self.monitor()
        site = self._site("a.com")
        site.access = discovery.LogTarget(state=discovery.LOG_UNRESOLVED, raw="logs/a.log")
        site.error = discovery.LogTarget(state=discovery.LOG_ABSENT)

        monitor.update_sources(self._result(site))

        self.assertEqual(monitor.watched_count, 0)

    def test_a_new_site_is_picked_up_and_starts_at_the_end(self):
        path = self.log_path("new.log")
        self.write(path, "history\n", mode="w")

        monitor = self.monitor()
        monitor.update_sources(self._result(self._site("new.com", access=path)))
        lines, stats = self.poll(monitor)

        self.assertEqual(lines, [])
        self.assertEqual(stats.first_seen, 1)

    def test_a_removed_site_stops_being_polled(self):
        path = self.log_path("gone.log")
        path.touch()
        monitor = self.monitor()
        monitor.update_sources(self._result(self._site("gone.com", access=path)))
        self.poll(monitor)

        added, removed = monitor.update_sources(self._result())
        self.write(path, "nobody is listening\n")
        lines, stats = self.poll(monitor)

        self.assertEqual(removed, [str(path)])
        self.assertEqual(lines, [])
        self.assertEqual(stats.files, 0)

    def test_state_for_a_removed_site_is_kept(self):
        """A site that flaps out of discovery must not lose its position."""
        path = self.log_path("flap.log")
        path.touch()
        monitor = self.monitor()
        site = self._site("flap.com", access=path)
        monitor.update_sources(self._result(site))
        self.poll(monitor)
        self.write(path, "one\n")
        self.poll(monitor)
        offset = monitor.offset_of(path)

        monitor.update_sources(self._result())
        monitor.update_sources(self._result(site))

        self.assertEqual(monitor.offset_of(path), offset)

        self.write(path, "two\n")
        self.assertEqual(self.poll(monitor)[0], ["two"])

    def test_a_changed_log_path_switches_files(self):
        old = self.log_path("old.log")
        new = self.log_path("new.log")
        old.touch()
        self.write(new, "history of the new file\n", mode="w")

        monitor = self.monitor()
        monitor.update_sources(self._result(self._site("a.com", access=old)))
        self.poll(monitor)

        monitor.update_sources(self._result(self._site("a.com", access=new)))
        self.write(old, "written to the old path\n")
        lines, _ = self.poll(monitor)

        self.assertEqual(lines, [])
        self.write(new, "written to the new path\n")
        self.assertEqual(self.poll(monitor)[0], ["written to the new path"])

    def test_a_shared_log_is_read_once_and_keeps_both_sites(self):
        shared = self.log_path("shared.log")
        shared.touch()

        monitor = self.monitor()
        monitor.update_sources(
            self._result(
                self._site("one.com", access=shared),
                self._site("two.com", access=shared),
            )
        )
        self.poll(monitor)
        self.write(shared, "one request\n")

        collected = _collect(monitor)

        self.assertEqual(monitor.watched_count, 1)
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0].sites, ("one.com", "two.com"))
        self.assertEqual(collected[0].site, "one.com")


class Symlinks(MonitorCase):
    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_a_symlinked_log_is_followed_through_to_its_target(self):
        target = self.log_path("real.log")
        link = self.log_path("link.log")
        target.touch()
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted here")

        monitor = self.monitor()
        self.watch(monitor, link)
        self.poll(monitor)

        self.write(target, "through the link\n")

        self.assertEqual(self.poll(monitor)[0], ["through the link"])
        # os.stat follows the link, so the inode is the target's.
        self.assertEqual(monitor.offset_of(link), target.stat().st_size)


class DaemonIntegration(MonitorCase):
    """The pieces the daemon wires together, without starting a daemon."""

    def _vhost(self, name, access, error):
        return (
            "server\n{\n"
            f"    server_name {name};\n"
            f"    access_log {access.as_posix()};\n"
            f"    error_log {error.as_posix()};\n"
            "}\n"
        )

    def test_discovery_feeds_the_monitor_and_offsets_persist(self):
        from aadoctor import daemon

        vhosts = self.root / "vhosts"
        vhosts.mkdir()
        access = self.log_path("site.log")
        error = self.log_path("site.error.log")
        access.touch()
        error.touch()
        (vhosts / "site.conf").write_text(
            self._vhost("site.com", access, error), encoding="utf-8"
        )

        monitor = self.monitor()
        result = discovery.discover_sites(vhosts)
        added, _ = monitor.update_sources(result)
        self.assertEqual(len(added), 2)

        # First poll registers both files at their end.
        stats = daemon.run_poll(monitor)
        self.assertEqual(stats.first_seen, 2)
        self.assertTrue(self.state_path.exists())

        self.write(access, "a request\n")
        self.write(error, "an error\n")
        stats = daemon.run_poll(monitor)

        self.assertEqual(stats.lines, 2)

        # A fresh monitor reads the persisted offsets and does not repeat them.
        restarted = self.monitor()
        restarted.update_sources(result)
        self.assertEqual(daemon.run_poll(restarted).lines, 0)

    def test_a_site_added_between_passes_is_followed(self):
        vhosts = self.root / "vhosts"
        vhosts.mkdir()
        first_log = self.log_path("first.log")
        first_log.touch()
        (vhosts / "first.conf").write_text(
            self._vhost("first.com", first_log, self.log_path("first.error.log")),
            encoding="utf-8",
        )

        monitor = self.monitor()
        monitor.update_sources(discovery.discover_sites(vhosts))
        self.poll(monitor)

        second_log = self.log_path("second.log")
        self.write(second_log, "history nobody asked for\n", mode="w")
        (vhosts / "second.conf").write_text(
            self._vhost("second.com", second_log, self.log_path("second.error.log")),
            encoding="utf-8",
        )

        added, _ = monitor.update_sources(discovery.discover_sites(vhosts))
        self.assertIn(str(second_log), added)

        lines, stats = self.poll(monitor)
        self.assertEqual(lines, [])
        self.assertEqual(stats.first_seen, 1)

        self.write(second_log, "new traffic\n")
        self.assertEqual(self.poll(monitor)[0], ["new traffic"])


class SourceSafety(unittest.TestCase):
    """The collector may read logs and nothing else (ADR-001).

    Checked against the source, so a future change that opens a log for
    writing fails here rather than on someone's server.
    """

    SOURCE = _support.ROOT / "src" / "aadoctor" / "collectors" / "logs.py"

    def _code_lines(self):
        text = self.SOURCE.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                yield number, line

    def test_logs_are_only_ever_opened_for_reading(self):
        for number, line in self._code_lines():
            if "open(" not in line or "fdopen" in line:
                continue
            self.assertIn('"rb"', line, f"logs.py:{number} opens a file for writing")

    def test_no_call_can_modify_a_log(self):
        forbidden = (
            "os.unlink(",
            "os.remove(",
            "os.rename(",
            "os.replace(",
            "os.chmod(",
            "os.chown(",
            ".truncate(",
            ".write(",
            ".writelines(",
            "shutil.",
            "setfacl",
        )
        for number, line in self._code_lines():
            for call in forbidden:
                self.assertNotIn(call, line, f"logs.py:{number} could modify a log")


class Summary(unittest.TestCase):
    def test_summary_counts_only(self):
        stats = collector.PollStats(files=3, lines=10, rotations=1)
        text = collector.summarize(stats)

        self.assertIn("10 lines from 3 files", text)
        self.assertIn("1 rotated", text)


def _collect(monitor):
    collected = []
    monitor.poll(collected.append)
    return collected


if __name__ == "__main__":
    unittest.main()
