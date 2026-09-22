"""aaPanel vhost discovery (SPEC-002).

Nothing here reads /www: the parser works on strings, and discovery is pointed
at fixture directories.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor import discovery
from aadoctor.discovery import aapanel

FIXTURES = _support.ROOT / "tests" / "fixtures" / "vhosts"


def fixture(name):
    return FIXTURES / name


def parse_fixture(name):
    path = fixture(name)
    return discovery.parse_vhost(path.read_text(encoding="utf-8"), path)


def one_site(name):
    sites = parse_fixture(name)
    assert len(sites) == 1, f"{name} produced {len(sites)} sites"
    return sites[0]


def write_vhosts(directory, files):
    """Create a vhost directory from {name: text}."""
    for name, text in files.items():
        (Path(directory) / name).write_text(text, encoding="utf-8")


BASIC = """
server
{
    listen 80;
    server_name %s;
    access_log /www/wwwlogs/%s.log;
    error_log /www/wwwlogs/%s.error.log;
}
"""


def basic_vhost(name):
    return BASIC % (name, name, name)


class Parsing(unittest.TestCase):
    def test_a_normal_site(self):
        site = one_site("basic.conf")

        self.assertEqual(site.name, "example.com")
        self.assertEqual(site.server_names, ["example.com"])
        self.assertEqual(site.access_log, Path("/www/wwwlogs/example.com.log"))
        self.assertEqual(site.error_log, Path("/www/wwwlogs/example.com.error.log"))
        self.assertFalse(site.unnamed)

    def test_multiple_server_names(self):
        site = one_site("multiple-server-names.conf")

        self.assertEqual(
            site.server_names,
            ["example.com", "www.example.com", "api.example.com"],
        )
        self.assertEqual(site.name, "example.com")
        self.assertEqual(site.aliases, ["www.example.com", "api.example.com"])

    def test_commented_directives_are_ignored(self):
        site = one_site("comments.conf")

        self.assertEqual(site.server_names, ["commented.com"])
        self.assertEqual(site.access_log, Path("/www/wwwlogs/commented.com.log"))
        self.assertEqual(site.error_log, Path("/www/wwwlogs/commented.com.error.log"))

    def test_access_log_format_is_recorded_and_the_path_is_clean(self):
        site = one_site("custom-log-format.conf")

        self.assertEqual(site.access_log, Path("/www/wwwlogs/formatted.com.log"))
        self.assertEqual(site.access.log_format, "main")

    def test_error_log_severity_is_not_part_of_the_path(self):
        site = one_site("custom-log-format.conf")

        self.assertEqual(site.error_log, Path("/www/wwwlogs/formatted.com.error.log"))

    def test_access_log_off_is_not_a_path(self):
        site = one_site("access-log-off.conf")

        self.assertEqual(site.access.state, discovery.LOG_DISABLED)
        self.assertIsNone(site.access_log)
        self.assertEqual(site.access.raw, "off")
        # The site is still discovered, and its error log still monitored.
        self.assertEqual(site.error_log, Path("/www/wwwlogs/quiet.com.error.log"))

    def test_missing_access_log_leaves_the_site_valid(self):
        site = one_site("missing-access-log.conf")

        self.assertEqual(site.access.state, discovery.LOG_ABSENT)
        self.assertIsNone(site.access_log)
        self.assertEqual(site.error_log, Path("/www/wwwlogs/no-access.com.error.log"))

    def test_missing_error_log_leaves_the_site_valid(self):
        site = one_site("missing-error-log.conf")

        self.assertEqual(site.error.state, discovery.LOG_ABSENT)
        self.assertEqual(site.access_log, Path("/www/wwwlogs/no-error.com.log"))

    def test_parsing_touches_no_files(self):
        """parse_vhost is pure: a path that does not exist is fine."""
        sites = discovery.parse_vhost(basic_vhost("nowhere.com"), Path("/nope/x.conf"))

        self.assertEqual(sites[0].name, "nowhere.com")
        self.assertIsNone(sites[0].access.exists)


class ServerBlocks(unittest.TestCase):
    def test_brace_on_its_own_line(self):
        """aaPanel writes 'server' and '{' on separate lines."""
        sites = discovery.parse_vhost(
            "server\n{\n server_name braces.com;\n}\n", Path("braces.conf")
        )

        self.assertEqual([site.name for site in sites], ["braces.com"])

    def test_brace_on_the_same_line(self):
        sites = discovery.parse_vhost(
            "server { server_name inline.com; }", Path("inline.conf")
        )

        self.assertEqual([site.name for site in sites], ["inline.com"])

    def test_redirect_and_ssl_blocks_are_one_site(self):
        site = one_site("ssl-redirect.conf")

        self.assertEqual(site.name, "secure.com")
        self.assertEqual(site.server_names, ["secure.com", "www.secure.com"])
        self.assertEqual(site.access_log, Path("/www/wwwlogs/secure.com.log"))
        self.assertEqual(site.error_log, Path("/www/wwwlogs/secure.com.error.log"))

    def test_blocks_with_different_names_stay_separate(self):
        text = (
            "server { server_name one.com; access_log /l/one.log; }\n"
            "server { server_name two.com; access_log /l/two.log; }\n"
        )
        sites = discovery.parse_vhost(text, Path("pair.conf"))

        self.assertEqual(sorted(site.name for site in sites), ["one.com", "two.com"])

    def test_a_file_with_no_server_block_yields_nothing(self):
        self.assertEqual(parse_fixture("not-a-vhost.conf"), [])

    def test_location_blocks_do_not_start_a_site(self):
        site = one_site("ssl-redirect.conf")

        self.assertEqual(site.config_path.name, "ssl-redirect.conf")


class Naming(unittest.TestCase):
    def test_catch_all_falls_back_to_the_file_name(self):
        site = one_site("default-catchall.conf")

        self.assertEqual(site.name, "default-catchall")
        self.assertTrue(site.unnamed)
        self.assertIn("_", site.server_names)
        self.assertTrue(any("server_name" in text for text in site.warnings))

    def test_wildcards_are_aliases_but_never_the_canonical_name(self):
        text = "server { server_name *.example.com example.com; }"
        site = discovery.parse_vhost(text, Path("wild.conf"))[0]

        self.assertEqual(site.name, "example.com")
        self.assertIn("*.example.com", site.server_names)

    def test_a_site_with_only_wildcards_is_named_after_its_file(self):
        text = "server { server_name *.example.com; }"
        site = discovery.parse_vhost(text, Path("/x/wild.conf"))[0]

        self.assertEqual(site.name, "wild")
        self.assertTrue(site.unnamed)

    def test_duplicate_server_names_are_removed_in_order(self):
        text = "server { server_name a.com b.com a.com; }"
        site = discovery.parse_vhost(text, Path("dup.conf"))[0]

        self.assertEqual(site.server_names, ["a.com", "b.com"])


class DuplicateDirectives(unittest.TestCase):
    def test_the_first_access_log_wins_and_the_rest_are_reported(self):
        site = one_site("duplicate-directives.conf")

        self.assertEqual(site.access_log, Path("/www/wwwlogs/doubled.com.log"))
        self.assertTrue(
            any("several access_log" in text for text in site.warnings),
            site.warnings,
        )

    def test_a_usable_path_beats_an_earlier_disabled_one(self):
        text = (
            "server { server_name mixed.com; access_log off;"
            " access_log /www/wwwlogs/mixed.log; }"
        )
        site = discovery.parse_vhost(text, Path("mixed.conf"))[0]

        self.assertEqual(site.access_log, Path("/www/wwwlogs/mixed.log"))


class OddValues(unittest.TestCase):
    def test_dev_null_counts_as_disabled(self):
        text = "server { server_name null.com; access_log /dev/null; }"
        site = discovery.parse_vhost(text, Path("null.conf"))[0]

        self.assertEqual(site.access.state, discovery.LOG_DISABLED)
        self.assertIsNone(site.access_log)

    def test_error_log_off_counts_as_disabled(self):
        text = "server { server_name quiet.com; error_log off; }"
        site = discovery.parse_vhost(text, Path("quiet.conf"))[0]

        self.assertEqual(site.error.state, discovery.LOG_DISABLED)

    def test_a_relative_path_is_not_guessed_at(self):
        text = "server { server_name rel.com; access_log logs/rel.log; }"
        site = discovery.parse_vhost(text, Path("rel.conf"))[0]

        self.assertEqual(site.access.state, discovery.LOG_UNRESOLVED)
        self.assertIsNone(site.access_log)
        self.assertEqual(site.access.raw, "logs/rel.log")
        self.assertTrue(any("relative" in text for text in site.warnings))

    def test_a_log_path_outside_wwwlogs_is_accepted(self):
        text = "server { server_name custom.com; access_log /var/log/custom.log; }"
        site = discovery.parse_vhost(text, Path("custom.conf"))[0]

        self.assertEqual(site.access_log, Path("/var/log/custom.log"))

    def test_an_include_is_reported_not_followed(self):
        text = "server { server_name inc.com; include /etc/nginx/logs.conf; }"
        site = discovery.parse_vhost(text, Path("inc.conf"))[0]

        self.assertTrue(any("include" in text for text in site.warnings), site.warnings)

    def test_unbalanced_braces_are_reported(self):
        site = one_site("malformed.conf")

        self.assertTrue(any("braces" in text for text in site.warnings), site.warnings)

    def test_a_missing_semicolon_is_reported_not_absorbed(self):
        """A path is trusted; a 'log format' containing a slash is not."""
        site = one_site("malformed.conf")

        self.assertEqual(site.access_log, Path("/www/wwwlogs/broken.com.log"))
        self.assertIsNone(site.access.log_format)
        self.assertTrue(
            any("semicolon" in text for text in site.warnings), site.warnings
        )

    def test_odd_bytes_do_not_raise(self):
        text = "server { server_name caf\udcffe.com; access_log /l/a.log; }"
        sites = discovery.parse_vhost(text, Path("odd.conf"))

        self.assertEqual(len(sites), 1)


class Discovery(unittest.TestCase):
    def test_missing_directory_is_a_warning_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = discovery.discover_sites(Path(tmp) / "absent")

        self.assertEqual(result.sites, [])
        self.assertTrue(result.warnings)
        self.assertIn("not found", result.warnings[0])

    def test_empty_directory_yields_no_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.sites, [])
        self.assertEqual(result.warnings, [])

    def test_one_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(tmp, {"example.com.conf": basic_vhost("example.com")})
            result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.names, ["example.com"])
        self.assertEqual(result.count_access(discovery.LOG_CONFIGURED), 1)
        self.assertEqual(result.count_error(discovery.LOG_CONFIGURED), 1)

    def test_many_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(
                tmp,
                {
                    "a.conf": basic_vhost("a.com"),
                    "b.conf": basic_vhost("b.com"),
                    "c.conf": basic_vhost("c.com"),
                },
            )
            result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.names, ["a.com", "b.com", "c.com"])
        self.assertEqual(result.site_count, 3)

    def test_only_conf_files_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(
                tmp,
                {
                    "site.conf": basic_vhost("site.com"),
                    "site.conf.bak": basic_vhost("backup.com"),
                    "notes.txt": basic_vhost("notes.com"),
                },
            )
            result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.names, ["site.com"])

    def test_directories_are_not_read_as_vhosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "subdir.conf").mkdir()
            write_vhosts(tmp, {"site.conf": basic_vhost("site.com")})
            result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.names, ["site.com"])

    def test_a_malformed_file_does_not_stop_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(
                tmp,
                {
                    "good.conf": basic_vhost("good.com"),
                    "bad.conf": fixture("malformed.conf").read_text(encoding="utf-8"),
                    "later.conf": basic_vhost("later.com"),
                },
            )
            result = discovery.discover_sites(Path(tmp))

        self.assertIn("good.com", result.names)
        self.assertIn("later.com", result.names)

    def test_an_unreadable_file_is_reported_and_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(tmp, {"denied.conf": basic_vhost("denied.com")})

            with mock.patch.object(Path, "read_text", side_effect=PermissionError):
                with self.assertLogs("aadoctor.discovery", level="WARNING"):
                    result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.sites, [])
        self.assertEqual(len(result.unreadable), 1)
        self.assertTrue(any("permission denied" in text for text in result.warnings))

    def test_a_parser_failure_does_not_stop_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(tmp, {"boom.conf": basic_vhost("boom.com")})

            with mock.patch.object(aapanel, "parse_vhost", side_effect=ValueError("x")):
                with self.assertLogs("aadoctor.discovery", level="WARNING"):
                    result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.sites, [])
        self.assertTrue(any("could not be parsed" in text for text in result.warnings))

    def test_duplicate_names_across_files_are_warned_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(
                tmp,
                {
                    "first.conf": basic_vhost("same.com"),
                    "second.conf": basic_vhost("same.com"),
                },
            )
            result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.site_count, 2)
        self.assertTrue(any("several vhosts" in text for text in result.warnings))


class ConfiguredVersusPresent(unittest.TestCase):
    def test_a_configured_log_that_does_not_exist_keeps_the_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(tmp, {"site.conf": basic_vhost("site.com")})
            result = discovery.discover_sites(Path(tmp))

        site = result.sites[0]
        self.assertTrue(site.access.configured)
        self.assertFalse(site.access.exists)
        self.assertTrue(site.access.missing_on_disk)
        self.assertEqual(result.missing_files[0][0], "site.com")

    def test_an_existing_log_is_reported_as_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            (logs / "here.log").write_text("", encoding="utf-8")

            text = (
                "server { server_name here.com; access_log %s; }"
                % (logs / "here.log").as_posix()
            )
            write_vhosts(tmp, {"here.conf": text})
            result = discovery.discover_sites(Path(tmp))

        site = [s for s in result.sites if s.name == "here.com"][0]
        self.assertTrue(site.access.exists)
        self.assertTrue(site.access.readable)
        self.assertFalse(site.access.missing_on_disk)

    def test_file_checks_can_be_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(tmp, {"site.conf": basic_vhost("site.com")})
            result = discovery.discover_sites(Path(tmp), check_files=False)

        self.assertIsNone(result.sites[0].access.exists)


class Ordering(unittest.TestCase):
    def test_sites_are_sorted_by_name_not_by_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(
                tmp,
                {
                    "zzz.conf": basic_vhost("aaa.com"),
                    "aaa.conf": basic_vhost("zzz.com"),
                    "mmm.conf": basic_vhost("mmm.com"),
                },
            )
            result = discovery.discover_sites(Path(tmp))

        self.assertEqual(result.names, ["aaa.com", "mmm.com", "zzz.com"])

    def test_repeated_runs_agree(self):
        result_one = discovery.discover_sites(FIXTURES)
        result_two = discovery.discover_sites(FIXTURES)

        self.assertEqual(result_one.names, result_two.names)
        self.assertEqual(result_one.all_warnings, result_two.all_warnings)


class RealFixtureDirectory(unittest.TestCase):
    """The committed fixture set, read as one aaPanel vhost directory."""

    def setUp(self):
        self.result = discovery.discover_sites(FIXTURES)

    def test_every_expected_site_is_found(self):
        self.assertEqual(
            self.result.names,
            [
                "commented.com",
                "default-catchall",
                "doubled.com",
                "example.com",
                "example.com",
                "formatted.com",
                "malformed",
                "no-access.com",
                "no-error.com",
                "quiet.com",
                "secure.com",
            ],
        )

    def test_the_helper_file_produced_no_site(self):
        sources = [site.config_path.name for site in self.result.sites]
        self.assertNotIn("not-a-vhost.conf", sources)

    def test_nothing_was_unreadable(self):
        self.assertEqual(self.result.unreadable, [])

    def test_counts_match_the_fixtures(self):
        self.assertEqual(self.result.count_access(discovery.LOG_DISABLED), 1)
        # missing-access-log.conf
        self.assertEqual(self.result.count_access(discovery.LOG_ABSENT), 1)
        # missing-error-log.conf, and malformed.conf where a missing semicolon
        # swallowed the error_log directive.
        self.assertEqual(self.result.count_error(discovery.LOG_ABSENT), 2)

    def test_discovery_writes_nothing(self):
        before = sorted((p.name, p.stat().st_mtime, p.stat().st_size) for p in FIXTURES.iterdir())
        discovery.discover_sites(FIXTURES)
        after = sorted((p.name, p.stat().st_mtime, p.stat().st_size) for p in FIXTURES.iterdir())

        self.assertEqual(before, after)


class Diff(unittest.TestCase):
    def _sites(self, *names):
        return [
            aapanel.SiteConfig(name=name, server_names=[name], config_path=Path("x"))
            for name in names
        ]

    def test_added_and_removed(self):
        added, removed = discovery.diff_sites(
            self._sites("a.com", "b.com"),
            self._sites("b.com", "c.com"),
        )

        self.assertEqual(added, ["c.com"])
        self.assertEqual(removed, ["a.com"])

    def test_no_change(self):
        sites = self._sites("a.com")
        self.assertEqual(discovery.diff_sites(sites, sites), ([], []))

    def test_summary_mentions_the_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_vhosts(tmp, {"site.conf": basic_vhost("site.com")})
            summary = discovery.summarize(discovery.discover_sites(Path(tmp)))

        self.assertIn("1 sites", summary)


if __name__ == "__main__":
    unittest.main()
