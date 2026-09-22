"""Static safety checks on the install and uninstall scripts.

These never execute the scripts. They assert the properties that make them
safe to run on a production aaPanel server: no write anywhere under /www, no
unguarded removal, and no systemctl call against a foreign unit.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest

import _support  # noqa: F401  (sys.path bootstrap)

SCRIPTS = ("install.sh", "uninstall.sh")

#: Commands that could modify something. Used to prove /www is only read.
MUTATORS = (
    "rm",
    "mv",
    "cp",
    "chmod",
    "chown",
    "chgrp",
    "setfacl",
    "truncate",
    "mkdir",
    "touch",
    "ln",
    "tee",
    "sed -i",
)


def read(name):
    return (_support.ROOT / name).read_text(encoding="utf-8")


def code_lines(text):
    """Script lines that are not blank and not a full-line comment."""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield number, line


def without_quoted(line):
    """The line with double- and single-quoted segments removed.

    Used to tell an actual command from the same word inside a message.
    """
    return re.sub(r"\"[^\"]*\"|'[^']*'", "", line)


class Syntax(unittest.TestCase):
    @unittest.skipIf(shutil.which("bash") is None, "bash not available")
    def test_scripts_parse(self):
        bash = shutil.which("bash")
        for name in SCRIPTS:
            result = subprocess.run(
                [bash, "-n", str(_support.ROOT / name)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")

    def test_scripts_fail_fast(self):
        for name in SCRIPTS:
            self.assertIn("set -euo pipefail", read(name), name)


class NonInvasive(unittest.TestCase):
    def test_www_is_only_ever_referenced_read_only(self):
        """/www may appear as a detection constant and nowhere else.

        Any other occurrence - a copy, a removal, a redirect - would break the
        guarantee in README.md section 4.
        """
        allowed = re.compile(r'^readonly [A-Z_]+="/www[^"]*"$')

        for name in SCRIPTS:
            for number, line in code_lines(read(name)):
                if "/www" not in line:
                    continue
                self.assertRegex(
                    line.strip(),
                    allowed,
                    f"{name}:{number} references /www outside a read-only constant",
                )

    def test_no_mutating_command_targets_www(self):
        for name in SCRIPTS:
            for number, line in code_lines(read(name)):
                if "/www" not in line:
                    continue
                for mutator in MUTATORS:
                    self.assertNotIn(
                        mutator + " ",
                        line,
                        f"{name}:{number} would modify something under /www",
                    )

    def test_only_aadoctor_service_is_controlled(self):
        """systemctl is only ever invoked on aaDoctor's own unit.

        Occurrences inside quoted messages do not count as invocations, so the
        check is made against the line with quoted segments removed.
        """
        for name in SCRIPTS:
            for number, line in code_lines(read(name)):
                if "systemctl" not in without_quoted(line):
                    continue
                acceptable = (
                    "daemon-reload" in line
                    or "${SERVICE_NAME}" in line
                    or "command -v systemctl" in line
                )
                self.assertTrue(
                    acceptable,
                    f"{name}:{number} calls systemctl on something other than aadoctor.service",
                )

    def test_service_name_constant_is_aadoctor(self):
        for name in SCRIPTS:
            self.assertIn('readonly SERVICE_NAME="aadoctor.service"', read(name), name)

    def test_no_cron_or_firewall_changes(self):
        forbidden = ("crontab", "ufw ", "iptables", "firewall-cmd", "nft ")
        for name in SCRIPTS:
            text = read(name)
            for token in forbidden:
                self.assertNotIn(token, text, f"{name} touches {token.strip()}")


class RemovalSafety(unittest.TestCase):
    def test_every_recursive_removal_goes_through_the_guard(self):
        """``rm -rf`` may appear only inside the allowlist guard functions."""
        pattern = re.compile(r'^rm -rf -- "\$\{target\}"$')

        for name in SCRIPTS:
            found = 0
            for number, line in code_lines(read(name)):
                if "rm -rf" not in line:
                    continue
                found += 1
                self.assertRegex(
                    line.strip(),
                    pattern,
                    f"{name}:{number} removes a path without the allowlist guard",
                )
            self.assertEqual(found, 1, f"{name} should contain exactly one removal site")

    def test_guards_reject_unexpected_paths(self):
        for name in SCRIPTS:
            self.assertIn("refusing to remove unexpected path", read(name), name)

    def test_removal_targets_are_literal_constants(self):
        """The guard's allowlist may only contain aaDoctor's own paths."""
        owned = {
            "/opt/aadoctor",
            "/opt/.aadoctor.stage",
            "/opt/.aadoctor.previous",
            "/etc/aadoctor",
            "/etc/aadoctor/config.toml",
            "/var/lib/aadoctor",
            "/var/log/aadoctor",
            "/usr/local/bin/aadoctor",
            "/etc/systemd/system/aadoctor.service",
        }
        # Created only with mkdir -p, never removed.
        shared = {"/usr/local/bin"}
        declared = re.compile(r'^readonly [A-Z_]+="(/[^"]*)"$')

        for name in SCRIPTS:
            for number, line in code_lines(read(name)):
                match = declared.match(line.strip())
                if not match:
                    continue
                value = match.group(1)
                if value.startswith("/www"):
                    continue  # read-only detection constant
                self.assertIn(value, owned | shared, f"{name}:{number} declares {value}")


class Uninstall(unittest.TestCase):
    def test_plain_uninstall_preserves_configuration_and_state(self):
        text = read("uninstall.sh")
        purge_block = text.split('if [ "${PURGE}" -eq 1 ]; then')[-1]

        self.assertIn('remove_owned_path "${CONFIG_DIR}"', purge_block)
        self.assertIn('remove_owned_path "${STATE_DIR}"', purge_block)
        self.assertIn('remove_owned_path "${LOG_DIR}"', purge_block)

    def test_final_message_claims_only_what_is_true(self):
        self.assertIn("No aaPanel files were modified.", read("uninstall.sh"))


class Install(unittest.TestCase):
    def test_existing_configuration_is_preserved(self):
        text = read("install.sh")
        self.assertIn('if [ -f "${CONFIG_FILE}" ]; then', text)
        self.assertIn("existing configuration preserved", text)

    def test_install_does_not_enable_the_service(self):
        """Installing must not start monitoring (README.md section 45)."""
        text = read("install.sh")
        self.assertNotIn("systemctl enable", text)
        self.assertNotIn("enable --now", text)

    def test_previous_installation_is_replaced_not_deleted_first(self):
        text = read("install.sh")
        self.assertIn('mv "${INSTALL_DIR}" "${PREVIOUS_DIR}"', text)
        self.assertIn('mv "${STAGE_DIR}" "${INSTALL_DIR}"', text)


if __name__ == "__main__":
    unittest.main()
