"""aaDoctor command line interface.

Only commands that actually do something are registered. Planned commands
(``top``, ``diagnose``, ``incidents``, ``show``, ``explain``) are deliberately
absent rather than present as stubs, so nothing can look implemented when it is
not. See SPEC-008 for the full intended surface.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from . import environment, service
from .config import ConfigError, load as load_config
from .paths import (
    CONFIG_FILE,
    DISPLAY_NAME,
    INSTALL_DIR,
    SERVICE_NAME,
    distribution_root,
)

# Exit codes (SPEC-008).
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ENV_NOT_READY = 2
EXIT_NOT_FOUND = 3
EXIT_DAEMON_NOT_RUNNING = 4
EXIT_PRIVILEGES = 5

UNINSTALL_SCRIPT = "uninstall.sh"


class _Parser(argparse.ArgumentParser):
    """argparse exits with 2 on a usage error; SPEC-008 says 1."""

    def error(self, message: str) -> "None":  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="aadoctor",
        description=f"{DISPLAY_NAME} - lightweight aaPanel + Nginx diagnostics",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{DISPLAY_NAME} {__version__}",
    )

    commands = parser.add_subparsers(dest="command", metavar="<command>")

    commands.add_parser("doctor", help="check the environment (read-only)")
    commands.add_parser("status", help="show aaDoctor and service state")
    commands.add_parser("enable", help=f"enable and start {SERVICE_NAME}")
    commands.add_parser("disable", help=f"stop and disable {SERVICE_NAME}")

    uninstall = commands.add_parser("uninstall", help="remove aaDoctor from this server")
    uninstall.add_argument(
        "--purge",
        action="store_true",
        help="also remove configuration, state and logs",
    )
    uninstall.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="do not ask for confirmation",
    )

    daemon = commands.add_parser("daemon", help="run the daemon in the foreground")
    daemon.add_argument("--verbose", action="store_true", help="debug logging")
    daemon.add_argument(
        "--log-file",
        metavar="PATH",
        help="write the log somewhere other than the default",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    handlers = {
        "doctor": cmd_doctor,
        "status": cmd_status,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "uninstall": cmd_uninstall,
        "daemon": cmd_daemon,
    }
    return handlers[args.command](args)


# --- commands ------------------------------------------------------------


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Report the environment. Reads only; fixes nothing."""
    print(f"{DISPLAY_NAME} environment check")
    print()

    checks = environment.check_all()
    for check in checks:
        label = f"[{check.status}] {check.name}"
        print(f"{label} - {check.detail}" if check.detail else label)

    print()

    config_status = _config_report()
    for line in config_status:
        print(line)
    print()

    if environment.ready(checks):
        print("Environment ready.")
        return EXIT_OK

    print("Environment not ready. No changes were made.")
    return EXIT_ENV_NOT_READY


def _config_report() -> List[str]:
    lines = []
    try:
        config = load_config()
    except ConfigError as exc:
        return [f"[FAIL] Configuration - {exc}"]

    if config.from_defaults:
        lines.append(f"[WARN] Configuration - {CONFIG_FILE} not found, using defaults")
    else:
        lines.append(f"[OK] Configuration - {config.source}")

    for key in config.unknown_keys:
        lines.append(f"[WARN] Configuration - unrecognized key {key}")

    return lines


def cmd_status(_args: argparse.Namespace) -> int:
    """Show what is actually known. Nothing is reported that does not exist."""
    print(f"{DISPLAY_NAME} {__version__}")
    print()

    print("Installed:")
    print("yes" if INSTALL_DIR.is_dir() else "no (running from a source checkout)")
    print()

    print("aaPanel:")
    print("detected" if environment.has_aapanel() else "not detected")
    print()

    print("Service:")
    print(_service_state())
    print()

    print("Configuration:")
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"error: {exc}")
        return EXIT_ENV_NOT_READY
    print(str(config.source) if config.source else "defaults (no config file)")
    print()

    print("Monitoring:")
    print("not implemented")

    return EXIT_OK


def _service_state() -> str:
    if not service.available():
        return "unknown (systemd not available)"
    try:
        active = service.is_active()
        enabled = service.is_enabled()
    except service.ServiceError as exc:
        return f"unknown ({exc})"

    if enabled == "not-found":
        return "not installed"
    return f"{active} ({enabled})"


def cmd_enable(_args: argparse.Namespace) -> int:
    return _lifecycle("enable", service.enable, f"{SERVICE_NAME} enabled and started")


def cmd_disable(_args: argparse.Namespace) -> int:
    return _lifecycle("disable", service.disable, f"{SERVICE_NAME} stopped and disabled")


def _lifecycle(action: str, call, success_message: str) -> int:
    """Run an idempotent systemctl action against aaDoctor's own unit."""
    if not service.available():
        print("systemd is required; systemctl was not found.", file=sys.stderr)
        return EXIT_ENV_NOT_READY

    if not environment.is_root():
        print(f"'aadoctor {action}' requires root.", file=sys.stderr)
        return EXIT_PRIVILEGES

    try:
        result = call()
    except service.ServiceError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ENV_NOT_READY

    if result.returncode != 0:
        print(service.failure_text(result), file=sys.stderr)
        return EXIT_DAEMON_NOT_RUNNING

    print(success_message)
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Delegate to uninstall.sh, which owns the removal logic.

    Keeping every ``rm`` in one script means one place to audit, and it keeps
    working when the Python installation is the thing that is broken.
    """
    script = _find_uninstall_script()
    if script is None:
        print(
            f"{UNINSTALL_SCRIPT} not found next to {distribution_root()}.\n"
            "Remove aaDoctor with the uninstall script from the release you installed.",
            file=sys.stderr,
        )
        return EXIT_NOT_FOUND

    if not environment.is_root():
        print("'aadoctor uninstall' requires root.", file=sys.stderr)
        return EXIT_PRIVILEGES

    command = ["/bin/bash", str(script)]
    if args.purge:
        command.append("--purge")
    if args.yes:
        command.append("--yes")

    completed = subprocess.run(command, check=False)
    return completed.returncode


def _find_uninstall_script() -> Optional[Path]:
    candidate = distribution_root() / UNINSTALL_SCRIPT
    return candidate if candidate.is_file() else None


def cmd_daemon(args: argparse.Namespace) -> int:
    from .daemon import run as run_daemon

    try:
        config = load_config()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ENV_NOT_READY

    log_file = Path(args.log_file) if args.log_file else None
    return run_daemon(config, log_file=log_file, verbose=args.verbose)
