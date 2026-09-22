"""Path safety.

These tests exist because a bug here removes the wrong directory on someone's
production server.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor import paths


class OwnedPaths(unittest.TestCase):
    def test_every_owned_path_is_absolute(self):
        for path in paths.OWNED_PATHS:
            self.assertTrue(path.as_posix().startswith("/"), path)

    def test_no_owned_path_lives_under_www(self):
        for path in paths.OWNED_PATHS:
            self.assertFalse(path.as_posix().startswith("/www"), path)
            self.assertFalse(paths.is_read_only_area(path), path)

    def test_uninstall_preserves_configuration_and_state(self):
        self.assertNotIn(paths.CONFIG_DIR, paths.UNINSTALL_PATHS)
        self.assertNotIn(paths.STATE_DIR, paths.UNINSTALL_PATHS)

    def test_purge_removes_everything_uninstall_does(self):
        for path in paths.UNINSTALL_PATHS:
            self.assertIn(path, paths.PURGE_PATHS)

    def test_purge_matches_the_documented_set(self):
        expected = {
            "/opt/aadoctor",
            "/etc/aadoctor",
            "/var/lib/aadoctor",
            "/var/log/aadoctor",
            "/etc/systemd/system/aadoctor.service",
            "/usr/local/bin/aadoctor",
        }
        self.assertEqual({path.as_posix() for path in paths.PURGE_PATHS}, expected)


class RemovalGuard(unittest.TestCase):
    def test_owned_paths_are_removable(self):
        for path in paths.PURGE_PATHS:
            self.assertEqual(paths.assert_removable(path), path)

    def test_aapanel_paths_are_refused(self):
        for candidate in ("/www", "/www/server", "/www/wwwroot", "/www/wwwlogs/site.log"):
            with self.assertRaises(paths.UnsafePathError):
                paths.assert_removable(Path(candidate))

    def test_unexpected_paths_are_refused(self):
        for candidate in ("/", "", "/etc", "/opt", "/var/lib", "/usr/local/bin"):
            with self.assertRaises(paths.UnsafePathError):
                paths.assert_removable(Path(candidate))

    def test_parent_of_an_owned_path_is_refused(self):
        with self.assertRaises(paths.UnsafePathError):
            paths.assert_removable(paths.INSTALL_DIR.parent)


class ReadOnlyAreas(unittest.TestCase):
    def test_aapanel_areas_are_read_only(self):
        for candidate in (
            paths.AAPANEL_DIR,
            paths.NGINX_VHOST_DIR,
            paths.WWWLOGS_DIR,
            Path("/www/wwwroot/example.com/index.php"),
        ):
            self.assertTrue(paths.is_read_only_area(candidate), candidate)

    def test_own_areas_are_not_read_only(self):
        for candidate in (paths.INSTALL_DIR, paths.STATE_DIR, paths.LOG_FILE):
            self.assertFalse(paths.is_read_only_area(candidate), candidate)


class Version(unittest.TestCase):
    def test_version_comes_from_the_version_file(self):
        expected = (_support.ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(paths.resolve_version(), expected)
        self.assertTrue(expected)


if __name__ == "__main__":
    unittest.main()
