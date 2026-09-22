"""Environment detection against fixture trees.

Detection takes a ``root`` so these tests never look at the real /www and
never need an aaPanel server.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor import environment


def make_aapanel_tree(root: Path, with_logs: bool = True) -> None:
    (root / environment.NGINX_VHOST_RELATIVE).mkdir(parents=True)
    if with_logs:
        (root / environment.WWWLOGS_RELATIVE).mkdir(parents=True)


class Detection(unittest.TestCase):
    def test_aapanel_detected_in_a_fixture_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_aapanel_tree(root)

            self.assertTrue(environment.has_aapanel(root))
            self.assertTrue(environment.nginx_vhost_dir(root).is_dir())

    def test_aapanel_absent_in_an_empty_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(environment.has_aapanel(Path(tmp)))

    def test_paths_are_derived_from_the_given_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(environment.aapanel_dir(root), root / "www/server/panel")
            self.assertEqual(environment.wwwlogs_dir(root), root / "www/wwwlogs")

    def test_default_root_is_the_real_filesystem(self):
        self.assertEqual(environment.aapanel_dir().as_posix(), "/www/server/panel")
        self.assertEqual(environment.wwwlogs_dir().as_posix(), "/www/wwwlogs")


class Checks(unittest.TestCase):
    def _named(self, checks):
        return {check.name: check for check in checks}

    def test_missing_aapanel_fails_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = environment.check_all(Path(tmp))

        named = self._named(checks)
        self.assertEqual(named["aaPanel"].status, environment.FAIL)
        self.assertFalse(environment.ready(checks))

    def test_present_aapanel_passes_its_own_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_aapanel_tree(root)
            checks = environment.check_all(root)

        named = self._named(checks)
        self.assertEqual(named["aaPanel"].status, environment.OK)
        self.assertEqual(named["Nginx vhost directory"].status, environment.OK)
        self.assertEqual(named["Site logs"].status, environment.OK)

    def test_missing_site_logs_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_aapanel_tree(root, with_logs=False)
            checks = environment.check_all(root)

        self.assertEqual(self._named(checks)["Site logs"].status, environment.WARN)

    def test_warnings_alone_do_not_make_the_environment_unready(self):
        warning_only = [
            environment.Check("a", environment.OK),
            environment.Check("b", environment.WARN),
        ]
        self.assertTrue(environment.ready(warning_only))

        with_failure = warning_only + [environment.Check("c", environment.FAIL)]
        self.assertFalse(environment.ready(with_failure))

    def test_every_check_has_a_known_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = environment.check_all(Path(tmp))

        self.assertTrue(checks)
        for check in checks:
            self.assertIn(check.status, (environment.OK, environment.WARN, environment.FAIL))
            self.assertTrue(check.name)

    def test_checks_do_not_create_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment.check_all(root)
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
