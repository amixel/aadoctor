"""CLI contract: argument parsing, version, exit codes.

Nothing here installs, enables or removes anything.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (sys.path bootstrap)
import _scenarios as scenarios

from aadoctor import __version__, cli, environment, runtime, storage
from aadoctor.analyzers import TrafficAggregator
from aadoctor.analyzers import incidents
from aadoctor.collectors.load import LoadSample
from aadoctor.parsers.nginx_access import AccessEvent
from aadoctor.parsers.nginx_error import ErrorEvent


def run(argv):
    """Run the CLI, returning (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exit_code:
            code = exit_code.code if exit_code.code is not None else 0
    return code, out.getvalue(), err.getvalue()


class Version(unittest.TestCase):
    def test_version_flag_prints_name_and_version(self):
        code, out, _ = run(["--version"])

        self.assertEqual(code, 0)
        self.assertIn("aaDoctor", out)
        self.assertIn(__version__, out)

    def test_version_is_not_the_unknown_fallback(self):
        self.assertNotEqual(__version__, "0.0.0-unknown")


class Usage(unittest.TestCase):
    def test_no_command_prints_help_and_reports_a_usage_error(self):
        code, out, _ = run([])

        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("usage:", out.lower())

    def test_unknown_command_exits_with_the_usage_code(self):
        code, _, err = run(["nonsense"])

        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertTrue(err)

    def test_help_lists_only_implemented_commands(self):
        code, out, _ = run(["--help"])

        self.assertEqual(code, 0)
        for command in (
            "doctor",
            "status",
            "sites",
            "top",
            "incidents",
            "show",
            "diagnose",
            "enable",
            "disable",
            "update",
            "uninstall",
            "daemon",
        ):
            self.assertIn(command, out)

    def test_help_shows_the_order_the_commands_are_used_in(self):
        # The command list alone does not say that `doctor` comes before
        # `enable`, or that nothing appears until traffic has arrived. Both
        # were real questions from the first server this ran on.
        code, out, _ = run(["--help"])

        self.assertEqual(code, 0)
        for step in ("doctor", "enable", "top", "incidents", "diagnose"):
            self.assertIn(step, out)
        self.assertIn("USAGE.md", out)

    def test_help_fits_eighty_columns(self):
        # SPEC-008: read over SSH during an incident, often on a phone.
        _, out, _ = run(["--help"])

        too_wide = [line for line in out.splitlines() if len(line) > 80]
        self.assertEqual(too_wide, [])

    def test_help_promises_nothing_the_tool_does_not_do(self):
        # SPEC-008 and ADR-001: no command here acts on the server, and the
        # help must not imply otherwise.
        _, out, _ = run(["--help"])
        lowered = out.lower()

        for forbidden in ("restart", "block", "fix", "clean", "repair"):
            self.assertNotIn(f" {forbidden} ", f" {lowered} ".replace("\n", " "))

    def test_planned_commands_are_not_pretending_to_exist(self):
        # SPEC-008 plans this; it is not implemented, so it is not registered.
        for command in ("explain",):
            code, _, _ = run([command])
            self.assertEqual(code, cli.EXIT_USAGE, command)


class Doctor(unittest.TestCase):
    def test_doctor_reports_checks_and_does_not_crash(self):
        code, out, _ = run(["doctor"])

        self.assertIn(code, (cli.EXIT_OK, cli.EXIT_ENV_NOT_READY))
        self.assertIn("environment check", out)
        self.assertTrue(any(marker in out for marker in ("[OK]", "[WARN]", "[FAIL]")))

    def test_doctor_reports_readiness_consistently_with_its_checks(self):
        code, out, _ = run(["doctor"])

        if environment.ready(environment.check_all()):
            self.assertEqual(code, cli.EXIT_OK)
            self.assertIn("Environment ready.", out)
        else:
            self.assertEqual(code, cli.EXIT_ENV_NOT_READY)
            self.assertIn("No changes were made.", out)


class Status(unittest.TestCase):
    def test_status_reports_only_what_exists(self):
        code, out, _ = run(["status"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn(__version__, out)
        self.assertIn("Service:", out)
        self.assertIn("Monitoring:", out)

    def test_status_does_not_claim_diagnosis_is_missing(self):
        # It said "diagnosis not implemented yet" for four phases, and this
        # test locked the sentence in place. It is implemented now.
        _, out, _ = run(["status"])

        self.assertNotIn("not implemented", out)
        self.assertIn("aadoctor diagnose", out)

    def test_status_exits_zero_with_no_daemon_running(self):
        # A state report that fails when there is no state to report is a
        # report nobody can script around.
        code, _, _ = run(["status"])
        self.assertEqual(code, cli.EXIT_OK)

    def test_status_does_not_report_traffic_leaders(self):
        _, out, _ = run(["status"])

        lowered = out.lower()
        # Counts and top lists belong to `top`; status stays a state report.
        for absent in ("requests", "top ip", "top path"):
            self.assertNotIn(absent, lowered)


class StatusRuntime(unittest.TestCase):
    """Load and open incident, read from what the daemon published."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "runtime.json"
        self._patch = mock.patch.object(runtime, "RUNTIME_FILE", self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def publish(self, load=None, incident=None):
        payload = runtime.build(TrafficAggregator(), load=load, incident=incident)
        storage.write_json(self.path, payload)

    def test_the_current_load_is_shown(self):
        self.publish(load=LoadSample(load1=1.82, load5=1.31, load15=0.92, cpu_count=4))
        _, out, _ = run(["status"])

        self.assertIn("Current load:", out)
        self.assertIn("1.82", out)
        self.assertIn("4 CPUs", out)
        self.assertIn("0.46 per core", out)

    def test_no_incident_open(self):
        self.publish(load=LoadSample(load1=0.5, load5=0.5, load15=0.5, cpu_count=4))
        _, out, _ = run(["status"])

        self.assertIn("Incident:", out)
        self.assertIn("none open", out)

    def test_an_open_incident_is_named(self):
        incident = incidents.Incident(
            id="2026-09-22T18-31-40",
            started_at="2026-09-22T18:31:40+00:00",
            status=incidents.OPEN,
            severity=incidents.CRITICAL,
            peak_load={"load_per_cpu": 2.95},
        )
        self.publish(
            load=LoadSample(load1=11.8, load5=6.9, load15=3.7, cpu_count=4),
            incident=incident,
        )
        _, out, _ = run(["status"])

        self.assertIn("OPEN since 2026-09-22T18:31:40", out)
        self.assertIn("2.95", out)

    def test_status_still_works_without_a_published_snapshot(self):
        code, out, _ = run(["status"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertNotIn("Current load:", out)


class Sites(unittest.TestCase):
    """`aadoctor sites` renders what discovery found (SPEC-002)."""

    FIXTURES = str(_support.ROOT / "tests" / "fixtures" / "vhosts")

    def test_table_lists_the_discovered_sites(self):
        code, out, _ = run(["sites", "--vhost-dir", self.FIXTURES])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("SITE", out)
        self.assertIn("example.com", out)
        self.assertIn("secure.com", out)

    def test_table_fits_eighty_columns(self):
        _, out, _ = run(["sites", "--vhost-dir", self.FIXTURES])

        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_a_disabled_access_log_is_shown_as_off(self):
        _, out, _ = run(["sites", "--vhost-dir", self.FIXTURES])

        row = [line for line in out.splitlines() if line.startswith("quiet.com")][0]
        self.assertIn("off", row)

    def test_nothing_configured_reads_differently_from_file_not_there(self):
        """'none' and 'missing' must not be confusable."""
        _, out, _ = run(["sites", "--vhost-dir", self.FIXTURES])
        rows = {line.split()[0]: line for line in out.splitlines() if line}

        # no-access.conf declares no access_log at all.
        self.assertIn("none", rows["no-access.com"])
        # commented.conf declares one; the file does not exist in the fixtures.
        self.assertIn("missing", rows["commented.com"])

    def test_json_output_is_valid_and_carries_the_paths(self):
        code, out, _ = run(["sites", "--json", "--vhost-dir", self.FIXTURES])
        data = json.loads(out)

        self.assertEqual(code, cli.EXIT_OK)
        names = [site["name"] for site in data["sites"]]
        self.assertIn("example.com", names)

        site = [s for s in data["sites"] if s["name"] == "formatted.com"][0]
        self.assertEqual(site["access_log"]["path"], "/www/wwwlogs/formatted.com.log")
        self.assertEqual(site["access_log"]["log_format"], "main")

    def test_an_empty_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(["sites", "--vhost-dir", tmp])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("No sites found", out)

    def test_sites_writes_nothing(self):
        directory = _support.ROOT / "tests" / "fixtures" / "vhosts"
        before = sorted((p.name, p.stat().st_mtime) for p in directory.iterdir())
        run(["sites", "--vhost-dir", self.FIXTURES])
        after = sorted((p.name, p.stat().st_mtime) for p in directory.iterdir())

        self.assertEqual(before, after)


class Top(unittest.TestCase):
    """`aadoctor top` renders the snapshot the daemon published (SPEC-005)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "runtime.json"
        self._patch = mock.patch.object(runtime, "RUNTIME_FILE", self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def publish(self, aggregator=None, age=0.0):
        aggregator = aggregator or self.busy_aggregator()
        payload = runtime.build(aggregator)
        payload["updated_at"] = time.time() - age
        storage.write_json(self.path, payload)

    def busy_aggregator(self):
        aggregator = TrafficAggregator()
        for _ in range(900):
            aggregator.add(
                AccessEvent(remote_addr="185.1.2.3", method="GET", path="/wp-cron.php",
                            status=200, site="loja.com.br")
            )
        for _ in range(100):
            aggregator.add(
                AccessEvent(remote_addr="9.9.9.9", method="GET", path="/",
                            status=502, site="cliente.com.br")
            )
        for _ in range(40):
            aggregator.add(ErrorEvent(level="error", kind="upstream_timeout",
                                      message="boom", site="loja.com.br"))
        return aggregator

    def test_without_a_snapshot_it_says_so_instead_of_guessing(self):
        code, _, err = run(["top"])

        self.assertEqual(code, cli.EXIT_DAEMON_NOT_RUNNING)
        self.assertIn("No traffic data yet", err)

    def test_it_shows_the_totals_and_the_leaders(self):
        self.publish()
        code, out, _ = run(["top"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("1,000", out)
        self.assertIn("loja.com.br", out)
        self.assertIn("/wp-cron.php", out)
        self.assertIn("185.1.2.3", out)
        self.assertIn("upstream_timeout", out)

    def test_it_reports_how_old_the_snapshot_is(self):
        self.publish(age=2)
        _, out, _ = run(["top"])

        self.assertIn("Updated", out)

    def test_a_stale_snapshot_is_never_shown_as_current(self):
        self.publish(age=600)
        _, out, _ = run(["top"])

        self.assertIn("old", out)
        self.assertNotIn("Updated 0s ago", out)

    def real_world_aggregator(self):
        """Names and paths as a real aaPanel server actually produces them.

        Taken from the first production install. The fixtures above are short
        and tidy, which is exactly why they hid a table that ran to more than
        140 columns on a real server.
        """
        aggregator = TrafficAggregator()
        long_paths = [
            "/politica/florianopolis-e-a-cidade-mais-cara-do-pais-para-comer-fora-de-casa/",
            "/dever-de-cooperar-com-o-consumidor-frente-as-enchentes-o-principio-da-"
            "manutencao-do-contrato-e-a-excecao-da-ruina/",
            "/evento/posse-do-departamento-de-responsabilidade-civil-e-aniversario-de-"
            "1-ano-das-lives-do-iargs/",
        ]
        for index, path in enumerate(long_paths):
            for _ in range(10 - index):
                aggregator.add(
                    AccessEvent(remote_addr="66.249.64.193", method="GET", path=path,
                                status=200, site="canaldopoder.net.br")
                )
        for _ in range(14):
            aggregator.add(
                AccessEvent(remote_addr="45.175.48.169", method="POST",
                            path="/xmlrpc.php", status=200, site="iargs.com.br")
            )
        return aggregator

    def test_long_real_world_paths_still_fit_eighty_columns(self):
        self.publish(self.real_world_aggregator())
        _, out, _ = run(["top"])

        self.assertIn("TOP PATHS", out)
        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_a_shortened_path_is_marked_as_shortened(self):
        self.publish(self.real_world_aggregator())
        _, out, _ = run(["top"])

        self.assertIn("...", out)

    def test_shortening_keeps_both_ends_so_rows_stay_distinguishable(self):
        # Two articles on one site share a long prefix. Cutting the tail would
        # render them as the same row.
        first = "/blog/2026/01/a-very-long-article-slug-that-goes-on/comentarios-a"
        second = "/blog/2026/01/a-very-long-article-slug-that-goes-on/comentarios-b"

        self.assertNotEqual(cli._fit(first, 40), cli._fit(second, 40))
        self.assertLessEqual(len(cli._fit(first, 40)), 40)
        self.assertEqual(cli._fit("/short", 40), "/short")

    def test_the_output_fits_eighty_columns(self):
        self.publish()
        _, out, _ = run(["top"])

        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_both_windows_are_available(self):
        self.publish()

        for window in ("1m", "5m", "60", "300"):
            code, _, _ = run(["top", "--window", window])
            self.assertEqual(code, cli.EXIT_OK, window)

    def test_an_unknown_window_is_a_usage_error(self):
        self.publish()
        code, _, err = run(["top", "--window", "3h"])

        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("1m or 5m", err)

    def test_one_site_can_be_inspected(self):
        self.publish()
        code, out, _ = run(["top", "--site", "loja.com.br"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("SITE: loja.com.br", out)
        self.assertIn("/wp-cron.php", out)
        self.assertNotIn("cliente.com.br", out)

    def test_an_unknown_site_is_reported(self):
        self.publish()
        code, _, err = run(["top", "--site", "nope.com"])

        self.assertEqual(code, cli.EXIT_NOT_FOUND)
        self.assertIn("no traffic", err)

    def test_json_output_is_valid(self):
        self.publish()
        code, out, _ = run(["top", "--json"])
        data = json.loads(out)

        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(data["total_requests"], 1000)
        self.assertEqual(data["sites"][0]["key"], "loja.com.br")

    def test_an_empty_window_says_so_rather_than_showing_nothing(self):
        self.publish(TrafficAggregator())
        _, out, _ = run(["top"])

        self.assertIn("No traffic in this window", out)

    def test_poor_parser_coverage_is_called_out(self):
        """Numbers built on a tenth of the lines must not look complete."""
        aggregator = TrafficAggregator()
        aggregator.add(AccessEvent(path="/", status=200, site="a.com"))
        for _ in range(20):
            aggregator.note_unparsed("access", "a.com")
        self.publish(aggregator)

        _, out, _ = run(["top"])

        self.assertIn("WARNING", out)
        self.assertIn("not parsed", out)
        self.assertIn("incomplete", out.lower())

    def test_it_states_what_happened_without_naming_a_cause(self):
        self.publish()
        _, out, _ = run(["top"])
        lowered = out.lower()

        for word in ("attack", "cause", "culprit", "responsible", "confidence"):
            self.assertNotIn(word, lowered)

    def test_top_writes_nothing(self):
        self.publish()
        before = self.path.stat().st_mtime
        run(["top"])

        self.assertEqual(self.path.stat().st_mtime, before)


class Incidents(unittest.TestCase):
    """`aadoctor incidents` and `show` read the incident files (SPEC-006)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self._patch = mock.patch.object(incidents, "INCIDENTS_DIR", self.directory)
        self._patch.start()
        self._cli_patch = mock.patch.object(cli, "INCIDENTS_DIR", self.directory)
        self._cli_patch.start()

    def tearDown(self):
        self._cli_patch.stop()
        self._patch.stop()
        self._tmp.cleanup()

    def write(self, incident_id, status="closed", traffic=None):
        payload = {
            "schema_version": 1,
            "id": incident_id,
            "status": status,
            "severity": "critical",
            "started_at": "2026-09-22T18:31:40+00:00",
            "peak_at": "2026-09-22T18:32:10+00:00",
            "ended_at": "2026-09-22T18:34:10+00:00" if status == "closed" else None,
            "system": {
                "cpu_count": 4,
                "start": {"load1": 5.82, "load5": 3.12, "load15": 1.8,
                          "load_per_cpu": 1.455},
                "peak": {"load1": 11.82, "load5": 6.91, "load15": 3.72,
                         "load_per_cpu": 2.955},
            },
            "traffic": {"start": None, "peak": traffic},
        }
        (self.directory / f"{incident_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return incident_id

    def traffic(self):
        aggregator = TrafficAggregator()
        for _ in range(8000):
            aggregator.add(
                AccessEvent(remote_addr="185.1.2.3", method="GET",
                            path="/wp-cron.php", status=200, site="site-a.com.br")
            )
        for _ in range(40):
            aggregator.add(ErrorEvent(level="error", kind="upstream_timeout",
                                      message="boom", site="site-a.com.br"))
        return {"300": aggregator.snapshot(window_seconds=300).as_dict()}

    def test_no_incidents_is_not_an_error(self):
        code, out, _ = run(["incidents"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("No incidents recorded", out)

    def test_incidents_are_listed_newest_first(self):
        self.write("2026-09-22T16-12-20")
        self.write("2026-09-22T18-31-40")
        code, out, _ = run(["incidents"])

        rows = [line for line in out.splitlines() if line.startswith("2026-")]
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(rows[0].startswith("2026-09-22T18-31-40"))

    def test_the_listing_shows_the_peak_per_core(self):
        self.write("2026-09-22T18-31-40")
        _, out, _ = run(["incidents"])

        # 2.955 per core, rendered to two places.
        self.assertIn("2.96", out)
        self.assertIn("critical", out)

    def test_an_open_incident_shows_as_open(self):
        self.write("2026-09-22T18-31-40", status="open")
        _, out, _ = run(["incidents"])

        self.assertIn("open", out)

    def test_an_interrupted_incident_says_so_rather_than_showing_a_dash(self):
        # A daemon restart mid-incident leaves the end unknown. SPEC-006 made
        # that a status so it would be visible; a bare dash in a duration
        # column reads as "could not compute" and hides it.
        self.write("2026-09-22T18-31-40", status="interrupted")
        _, out, _ = run(["incidents"])

        self.assertIn("interrupted", out)
        row = [line for line in out.splitlines() if line.startswith("2026-")][0]
        self.assertNotIn(" -  ", row)

    def test_the_listing_still_fits_eighty_columns_with_that_word(self):
        self.write("2026-09-22T18-31-40", status="interrupted")
        _, out, _ = run(["incidents"])

        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_the_limit_is_respected(self):
        for index in range(5):
            self.write(f"2026-09-22T18-31-4{index}")
        _, out, _ = run(["incidents", "--limit", "2"])

        rows = [line for line in out.splitlines() if line.startswith("2026-")]
        self.assertEqual(len(rows), 2)

    def test_the_listing_fits_eighty_columns(self):
        self.write("2026-09-22T18-31-40")
        _, out, _ = run(["incidents"])

        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_listing_as_json(self):
        self.write("2026-09-22T18-31-40")
        code, out, _ = run(["incidents", "--json"])
        data = json.loads(out)

        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(data[0]["id"], "2026-09-22T18-31-40")

    def test_show_renders_the_load_and_the_traffic(self):
        self.write("2026-09-22T18-31-40", traffic=self.traffic())
        code, out, _ = run(["show", "2026-09-22T18-31-40"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("INCIDENT 2026-09-22T18-31-40", out)
        self.assertIn("CPUs:          4", out)
        self.assertIn("11.82", out)
        self.assertIn("site-a.com.br", out)
        self.assertIn("/wp-cron.php", out)
        self.assertIn("185.1.2.3", out)
        self.assertIn("upstream_timeout", out)

    def test_show_states_no_cause(self):
        self.write("2026-09-22T18-31-40", traffic=self.traffic())
        _, out, _ = run(["show", "2026-09-22T18-31-40"])
        lowered = out.lower()

        for word in ("cause", "responsible", "culprit", "attack", "confidence"):
            self.assertNotIn(word, lowered)

    def test_show_reports_the_window_coverage(self):
        """It must not claim five minutes of evidence it does not have."""
        self.write("2026-09-22T18-31-40", traffic=self.traffic())
        _, out, _ = run(["show", "2026-09-22T18-31-40"])

        self.assertIn("Window:", out)

    def test_show_on_an_unknown_incident(self):
        code, _, err = run(["show", "2026-01-01T00-00-00"])

        self.assertEqual(code, cli.EXIT_NOT_FOUND)
        self.assertIn("No incident", err)

    def test_show_on_an_incident_with_no_traffic_recorded(self):
        self.write("2026-09-22T18-31-40", traffic=None)
        code, out, _ = run(["show", "2026-09-22T18-31-40"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("No traffic was recorded", out)

    def test_show_on_a_malformed_file_does_not_crash(self):
        (self.directory / "2026-09-22T18-31-40.json").write_text("{ broken", "utf-8")

        with self.assertLogs("aadoctor.storage", level="WARNING"):
            code, _, err = run(["show", "2026-09-22T18-31-40"])

        self.assertEqual(code, cli.EXIT_NOT_FOUND)
        self.assertTrue(err)

    def test_show_on_an_open_incident(self):
        self.write("2026-09-22T18-31-40", status="open")
        code, out, _ = run(["show", "2026-09-22T18-31-40"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Status:   OPEN", out)
        self.assertIn("Ended:    -", out)

    def test_show_as_json(self):
        self.write("2026-09-22T18-31-40")
        code, out, _ = run(["show", "2026-09-22T18-31-40", "--json"])
        data = json.loads(out)

        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(data["system"]["peak"]["load_per_cpu"], 2.955)

    def test_an_id_that_is_a_path_is_refused(self):
        code, _, _ = run(["show", "../../etc/passwd"])

        self.assertEqual(code, cli.EXIT_NOT_FOUND)

    def test_show_stays_factual_and_names_no_cause(self):
        # `show` renders what was measured; `diagnose` renders a reading of it.
        # If `show` ever started concluding, nobody could check the conclusion.
        self.write("2026-09-22T18-31-40", traffic=self.traffic())
        _, out, _ = run(["show", "2026-09-22T18-31-40"])

        for word in ("SUSPECT", "CONFIDENCE", "PROBABLE", "FINDINGS",
                     "ONE_SITE_DOMINATING", "responsible"):
            self.assertNotIn(word, out)


class Diagnose(unittest.TestCase):
    """`aadoctor diagnose` - the only command that interprets (SPEC-007)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self._patch = mock.patch.object(incidents, "INCIDENTS_DIR", self.directory)
        self._patch.start()
        self._cli_patch = mock.patch.object(cli, "INCIDENTS_DIR", self.directory)
        self._cli_patch.start()

    def tearDown(self):
        self._cli_patch.stop()
        self._patch.stop()
        self._tmp.cleanup()

    def write(self, record):
        path = self.directory / f"{record['id']}.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return record["id"]

    def dominant(self, incident_id="2026-09-22T18-31-40"):
        record = scenarios.dominant_site_scenario()
        record["id"] = incident_id
        return self.write(record)

    def test_no_incidents_is_not_an_error(self):
        code, out, _ = run(["diagnose"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("No incidents recorded", out)

    def test_without_an_id_it_diagnoses_the_most_recent_incident(self):
        self.dominant("2026-09-22T16-12-20")
        self.dominant("2026-09-22T18-31-40")
        code, out, _ = run(["diagnose"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("2026-09-22T18-31-40", out)
        self.assertNotIn("2026-09-22T16-12-20", out)

    def test_an_older_incident_can_be_diagnosed_by_id(self):
        self.dominant("2026-09-22T16-12-20")
        self.dominant("2026-09-22T18-31-40")
        _, out, _ = run(["diagnose", "2026-09-22T16-12-20"])

        self.assertIn("2026-09-22T16-12-20", out)

    def test_an_unknown_id_exits_three(self):
        code, _, err = run(["diagnose", "2020-01-01T00-00-00"])

        self.assertEqual(code, cli.EXIT_NOT_FOUND)
        self.assertIn(str(self.directory), err)

    def test_an_id_that_is_a_path_never_reaches_the_filesystem(self):
        code, _, _ = run(["diagnose", "../../etc/passwd"])
        self.assertEqual(code, cli.EXIT_NOT_FOUND)

    def test_the_report_names_the_site_the_path_and_the_address(self):
        self.dominant()
        _, out, _ = run(["diagnose"])

        self.assertIn("PRIMARY SITE", out)
        self.assertIn("site-a.com.br", out)
        self.assertIn("PRIMARY PATH", out)
        self.assertIn("/wp-cron.php", out)
        self.assertIn("ASSOCIATED IP", out)
        self.assertIn("185.1.2.3", out)

    def test_it_says_path_rather_than_url(self):
        # What aggregation holds is the path; the query string is not counted.
        self.dominant()
        _, out, _ = run(["diagnose"])

        self.assertIn("PRIMARY PATH", out)
        self.assertNotIn("PRIMARY URL", out)

    def test_every_claim_is_followed_by_its_numbers(self):
        self.dominant()
        _, out, _ = run(["diagnose"])

        self.assertIn("EVIDENCE", out)
        self.assertIn("84.2%", out)
        self.assertIn("8,000 of 9,500", out)
        self.assertIn("62.5%", out)

    def test_the_findings_are_listed_with_their_confidence(self):
        self.dominant()
        _, out, _ = run(["diagnose"])

        self.assertIn("FINDINGS", out)
        self.assertIn("ONE_SITE_DOMINATING", out)
        self.assertIn("UPSTREAM_TIMEOUT", out)
        self.assertIn("CONFIDENCE", out)
        self.assertIn("VERY HIGH", out)

    def test_it_never_states_the_cause_as_proven(self):
        self.dominant()
        _, out, _ = run(["diagnose"])
        lowered = out.lower()

        for word in ("root cause", "caused by", "guaranteed", "definitive"):
            self.assertNotIn(word, lowered)
        self.assertIn("evidence points to", lowered)

    def test_it_says_a_cause_outside_the_logs_cannot_appear(self):
        self.dominant()
        _, out, _ = run(["diagnose"])
        self.assertIn("cannot appear here", out)

    def test_an_ip_concentration_is_never_called_an_attack(self):
        self.dominant()
        _, out, _ = run(["diagnose"])
        lowered = out.lower()

        # The word appears exactly once, and only to say it is not one.
        self.assertIn("cdn", lowered)
        self.assertIn("rather than an attack", lowered)
        self.assertEqual(lowered.count("attack"), 1)
        for word in ("ddos", "block", "firewall", "ban ", "malicious"):
            self.assertNotIn(word, lowered)

    def test_a_distributed_incident_gets_no_invented_suspect(self):
        record = scenarios.distributed_scenario()
        record["id"] = "2026-09-22T18-31-40"
        self.write(record)
        code, out, _ = run(["diagnose"])

        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("No clear log-based cause identified", out)
        self.assertNotIn("PRIMARY SITE", out)

    def test_a_failing_site_is_found_without_dominating_the_traffic(self):
        record = scenarios.error_concentration_scenario()
        record["id"] = "2026-09-22T18-31-40"
        self.write(record)
        _, out, _ = run(["diagnose"])

        self.assertIn("site-b.com.br", out)
        self.assertIn("UPSTREAM_TIMEOUT", out)

    def test_rules_that_could_not_be_evaluated_are_shown_apart(self):
        record = scenarios.distributed_scenario()
        record["id"] = "2026-09-22T18-31-40"
        self.write(record)
        _, out, _ = run(["diagnose"])

        self.assertIn("NOT EVALUABLE", out)
        self.assertIn("no error log data", out)

    def test_the_report_fits_eighty_columns(self):
        self.dominant()
        _, out, _ = run(["diagnose"])

        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_the_inconclusive_report_fits_eighty_columns_too(self):
        # Its longest line is the explanation of why there is no answer, which
        # is prose and has to be wrapped rather than run off the terminal.
        record = scenarios.distributed_scenario()
        record["id"] = "2026-09-22T18-31-40"
        self.write(record)
        _, out, _ = run(["diagnose"])

        for line in out.splitlines():
            self.assertLessEqual(len(line), 80, line)

    def test_the_report_carries_no_control_characters(self):
        self.dominant()
        _, out, _ = run(["diagnose"])
        self.assertNotIn("\x1b", out)

    def test_json_output_exposes_the_whole_document(self):
        self.dominant()
        code, out, _ = run(["diagnose", "--json"])
        data = json.loads(out)

        self.assertEqual(code, cli.EXIT_OK)
        for key in ("incident", "diagnosis", "findings", "not_evaluable",
                    "evidence", "confidence", "data_quality", "ruleset_version"):
            self.assertIn(key, data)
        self.assertEqual(data["diagnosis"]["primary_site"], "site-a.com.br")

    def test_diagnosing_does_not_write_anything(self):
        incident_id = self.dominant()
        path = self.directory / f"{incident_id}.json"
        before = path.read_bytes()
        listing = sorted(item.name for item in self.directory.iterdir())

        run(["diagnose"])
        run(["diagnose", "--json"])

        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(item.name for item in self.directory.iterdir()), listing)

    def test_rendering_the_same_incident_twice_is_identical(self):
        self.dominant()
        _, first, _ = run(["diagnose"])
        _, second, _ = run(["diagnose"])
        self.assertEqual(first, second)


class Privileges(unittest.TestCase):
    @unittest.skipIf(environment.is_root(), "behaviour under test only applies without root")
    def test_enable_refuses_without_root(self):
        code, _, err = run(["enable"])

        self.assertIn(code, (cli.EXIT_PRIVILEGES, cli.EXIT_ENV_NOT_READY))
        self.assertTrue(err)

    @unittest.skipIf(environment.is_root(), "behaviour under test only applies without root")
    def test_disable_refuses_without_root(self):
        code, _, err = run(["disable"])

        self.assertIn(code, (cli.EXIT_PRIVILEGES, cli.EXIT_ENV_NOT_READY))
        self.assertTrue(err)

    @unittest.skipIf(environment.is_root(), "behaviour under test only applies without root")
    def test_uninstall_refuses_without_root(self):
        code, _, err = run(["uninstall"])

        self.assertIn(code, (cli.EXIT_PRIVILEGES, cli.EXIT_NOT_FOUND))
        self.assertTrue(err)


class ExitCodes(unittest.TestCase):
    def test_codes_are_distinct(self):
        codes = [
            cli.EXIT_OK,
            cli.EXIT_USAGE,
            cli.EXIT_ENV_NOT_READY,
            cli.EXIT_NOT_FOUND,
            cli.EXIT_DAEMON_NOT_RUNNING,
            cli.EXIT_PRIVILEGES,
        ]
        self.assertEqual(len(codes), len(set(codes)))


if __name__ == "__main__":
    unittest.main()
