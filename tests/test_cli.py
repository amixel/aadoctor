"""CLI contract: argument parsing, version, exit codes.

Nothing here installs, enables or removes anything.
"""

from __future__ import annotations

import contextlib
import io
import unittest

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor import __version__, cli, environment


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
        for command in ("doctor", "status", "enable", "disable", "uninstall", "daemon"):
            self.assertIn(command, out)

    def test_planned_commands_are_not_pretending_to_exist(self):
        # SPEC-008 plans these; none is implemented, so none is registered.
        for command in ("top", "diagnose", "incidents", "show", "explain", "update"):
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
        # Monitoring is not implemented; status must say so rather than invent
        # numbers (README.md section 40).
        self.assertIn("not implemented", out)

    def test_status_does_not_report_traffic_or_incidents(self):
        _, out, _ = run(["status"])

        lowered = out.lower()
        for absent in ("requests", "incident", "load/core", "top ip"):
            self.assertNotIn(absent, lowered)


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
