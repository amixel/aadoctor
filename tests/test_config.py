"""Configuration loading, including the behaviour install/upgrade depends on."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor import config


class Defaults(unittest.TestCase):
    def test_missing_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = config.load(Path(tmp) / "absent.toml")

        self.assertTrue(loaded.from_defaults)
        self.assertIsNone(loaded.source)
        self.assertTrue(loaded.get("monitor", "enabled"))
        self.assertFalse(loaded.get("mysql", "enabled"))
        self.assertFalse(loaded.get("ai", "enabled"))

    def test_optional_integrations_are_off_by_default(self):
        self.assertFalse(config.DEFAULTS["mysql"]["enabled"])
        self.assertFalse(config.DEFAULTS["ai"]["enabled"])

    def test_shipped_example_matches_the_built_in_defaults(self):
        example = _support.ROOT / "config.example.toml"
        loaded = config.load(example)

        self.assertFalse(loaded.from_defaults)
        self.assertEqual(loaded.unknown_keys, [])
        for section, values in config.DEFAULTS.items():
            for key, value in values.items():
                self.assertEqual(loaded.get(section, key), value, f"[{section}] {key}")


class Loading(unittest.TestCase):
    def _write(self, tmp, text):
        target = Path(tmp) / "config.toml"
        target.write_text(text, encoding="utf-8")
        return target

    def test_file_values_override_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(tmp, "[monitor]\nenabled = false\n")
            loaded = config.load(target)

        self.assertFalse(loaded.get("monitor", "enabled"))
        self.assertEqual(loaded.source, target)

    def test_unknown_keys_are_reported_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(tmp, "[monitor]\nenabled = true\nenabledd = true\n")
            loaded = config.load(target)

        self.assertIn("[monitor] enabledd", loaded.unknown_keys)

    def test_wrong_type_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(tmp, '[monitor]\nenabled = "yes"\n')
            with self.assertRaises(config.ConfigError):
                config.load(target)

    def test_malformed_file_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._write(tmp, "[monitor\nenabled = true\n")
            with self.assertRaises(config.ConfigError) as caught:
                config.load(target)

        self.assertIn(str(target), str(caught.exception))


class MinimalParser(unittest.TestCase):
    """The fallback parser used on Python older than 3.11 (ADR-008)."""

    def test_parses_the_shipped_example(self):
        text = (_support.ROOT / "config.example.toml").read_text(encoding="utf-8")
        parsed = config._parse_minimal_toml(text)

        self.assertEqual(parsed["monitor"]["enabled"], True)
        self.assertEqual(parsed["mysql"]["enabled"], False)
        self.assertEqual(parsed["ai"]["enabled"], False)

    def test_supported_value_types(self):
        parsed = config._parse_minimal_toml(
            "[s]\n"
            "flag = true\n"
            "off = false\n"
            "count = 10\n"
            "ratio = 1.5\n"
            'name = "value"\n'
        )
        self.assertEqual(
            parsed["s"],
            {"flag": True, "off": False, "count": 10, "ratio": 1.5, "name": "value"},
        )

    def test_comments_and_blank_lines_are_ignored(self):
        parsed = config._parse_minimal_toml(
            "# leading comment\n\n[s]\nflag = true  # trailing comment\n"
        )
        self.assertEqual(parsed["s"]["flag"], True)

    def test_hash_inside_a_string_is_not_a_comment(self):
        parsed = config._parse_minimal_toml('[s]\nname = "a#b"\n')
        self.assertEqual(parsed["s"]["name"], "a#b")

    def test_key_outside_a_section_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config._parse_minimal_toml("flag = true\n")

    def test_unsupported_value_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config._parse_minimal_toml("[s]\nitems = [1, 2]\n")

    def test_error_names_the_line(self):
        with self.assertRaises(config.ConfigError) as caught:
            config._parse_minimal_toml("[s]\nbroken\n")
        self.assertIn("line 2", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
