"""aaDoctor command line interface.

Only commands that actually do something are registered. Planned commands
(``top``, ``diagnose``, ``incidents``, ``show``, ``explain``) are deliberately
absent rather than present as stubs, so nothing can look implemented when it is
not. See SPEC-008 for the full intended surface.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from . import discovery, environment, service, storage
from .config import ConfigError, load as load_config
from .paths import (
    CONFIG_FILE,
    DISPLAY_NAME,
    INSTALL_DIR,
    SERVICE_NAME,
    STATE_FILE,
    distribution_root,
)

# Exit codes (SPEC-008).
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ENV_NOT_READY = 2
EXIT_NOT_FOUND = 3
EXIT_DAEMON_NOT_RUNNING = 4
EXIT_PRIVILEGES = 5

INSTALL_SCRIPT = "install.sh"
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

    sites = commands.add_parser("sites", help="list the sites discovered in aaPanel")
    sites.add_argument("--json", action="store_true", help="machine-readable output")
    sites.add_argument(
        "--vhost-dir",
        metavar="PATH",
        help="read vhosts from another directory instead of aaPanel's",
    )

    commands.add_parser("enable", help=f"enable and start {SERVICE_NAME}")
    commands.add_parser("disable", help=f"stop and disable {SERVICE_NAME}")

    update = commands.add_parser("update", help="install a newer published release")
    update.add_argument(
        "--version",
        metavar="X.Y.Z",
        help="install this exact release instead of the latest",
    )
    update.add_argument(
        "--force",
        action="store_true",
        help="reinstall even if that version is already installed",
    )

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
        "sites": cmd_sites,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "update": cmd_update,
        "uninstall": cmd_uninstall,
        "daemon": cmd_daemon,
    }
    return handlers[args.command](args)


# --- commands ------------------------------------------------------------


#: Warnings shown by doctor before the list is cut short.
MAX_WARNINGS = 10


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Report the environment and what was discovered. Reads only."""
    print(f"{DISPLAY_NAME} environment check")
    print()

    checks = environment.check_all()
    for check in checks:
        label = f"[{check.status}] {check.name}"
        print(f"{label} - {check.detail}" if check.detail else label)

    print()

    for line in _config_report():
        print(line)
    print()

    vhost_dir = environment.nginx_vhost_dir()
    if vhost_dir.is_dir():
        _print_discovery_report(discovery.discover_sites())

    if environment.ready(checks):
        print("Environment ready.")
        return EXIT_OK

    print("Environment not ready. No changes were made.")
    return EXIT_ENV_NOT_READY


def _print_discovery_report(result: "discovery.DiscoveryResult") -> None:
    """Site counts and what is wrong with them, from the real vhost files."""
    print(f"Sites found:       {result.site_count}")
    print(f"Access logs:       {result.count_access(discovery.LOG_CONFIGURED)} configured")
    print(f"Error logs:        {result.count_error(discovery.LOG_CONFIGURED)} configured")

    missing = result.missing_files
    if missing:
        print(f"Configured logs not on disk: {len(missing)}")

    warnings = list(result.all_warnings)
    warnings += [f"{name}: configured log does not exist ({path})" for name, path in missing]

    if warnings:
        print()
        print("Warnings:")
        for text in warnings[:MAX_WARNINGS]:
            print(f"- {text}")
        if len(warnings) > MAX_WARNINGS:
            print(f"- ... and {len(warnings) - MAX_WARNINGS} more")

    print()


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

    if environment.nginx_vhost_dir().is_dir():
        result = discovery.discover_sites()
        print("Sites:")
        print(result.site_count)
        print()
        print("Access logs:")
        print(result.count_access(discovery.LOG_CONFIGURED))
        print()
        print("Error logs:")
        print(result.count_error(discovery.LOG_CONFIGURED))
        print()
        print("Logs being followed:")
        print(_followed_count(result))
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
    print("following logs; parsing not implemented yet")

    return EXIT_OK


def cmd_sites(args: argparse.Namespace) -> int:
    """List what discovery found. Read-only, and wide enough for SSH."""
    vhost_dir = Path(args.vhost_dir) if args.vhost_dir else None
    result = discovery.discover_sites(vhost_dir)

    if args.json:
        print(json.dumps(_sites_as_data(result), indent=2, sort_keys=True))
        return EXIT_OK

    if not result.sites:
        print(f"No sites found in {result.vhost_dir}")
        for text in result.warnings:
            print(f"- {text}", file=sys.stderr)
        return EXIT_OK

    rows = [
        (site.name, _log_cell(site.access), _log_cell(site.error))
        for site in result.sites
    ]

    name_width = max([len(row[0]) for row in rows] + [len("SITE")])
    access_width = max([len(row[1]) for row in rows] + [len("ACCESS")])

    print(f"{'SITE'.ljust(name_width)}  {'ACCESS'.ljust(access_width)}  ERROR")
    for name, access, error in rows:
        print(f"{name.ljust(name_width)}  {access.ljust(access_width)}  {error}")

    warnings = result.all_warnings
    if warnings:
        print()
        print(f"{len(warnings)} warning(s); run 'aadoctor doctor' for details")

    return EXIT_OK


def _log_cell(target: "discovery.LogTarget") -> str:
    """One word per log, so the table stays readable at 80 columns.

    'none' and 'missing' are different answers: nothing is configured, versus
    a path is configured but the file is not there yet.
    """
    if target.state != discovery.LOG_CONFIGURED:
        return {
            discovery.LOG_DISABLED: "off",
            discovery.LOG_UNRESOLVED: "?",
        }.get(target.state, "none")
    return "missing" if target.exists is False else "yes"


def _sites_as_data(result: "discovery.DiscoveryResult") -> dict:
    return {
        "vhost_dir": str(result.vhost_dir),
        "warnings": result.all_warnings,
        "sites": [
            {
                "name": site.name,
                "server_names": site.server_names,
                "config_path": str(site.config_path),
                "unnamed": site.unnamed,
                "access_log": _target_as_data(site.access),
                "error_log": _target_as_data(site.error),
                "warnings": site.warnings,
            }
            for site in result.sites
        ],
    }


def _target_as_data(target: "discovery.LogTarget") -> dict:
    return {
        "state": target.state,
        "path": str(target.path) if target.path else None,
        "log_format": target.log_format,
        "exists": target.exists,
    }


def _followed_count(result: "discovery.DiscoveryResult") -> str:
    """How many log files the daemon holds a position in.

    Counted from the state the daemon actually wrote, not guessed from the
    configuration. When that state cannot be read - it is root-owned - the
    number of distinct configured log files is reported instead, and labelled
    as such rather than passed off as live monitoring.
    """
    distinct = {
        str(target.path)
        for site in result.sites
        for target in (site.access, site.error)
        if target.configured and target.path is not None
    }

    state = storage.read_json(STATE_FILE)
    if isinstance(state, dict) and isinstance(state.get("files"), dict):
        return f"{len(state['files'])} (from {STATE_FILE})"

    return f"{len(distinct)} configured; the daemon has not recorded any offsets yet"


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


def cmd_update(args: argparse.Namespace) -> int:
    """Delegate to install.sh in release mode.

    An update *is* an install from a verified release: download, check the
    SHA256, replace /opt/aadoctor, preserve configuration and state, restart the
    service only if it was running. There is no second implementation of that
    sequence.
    """
    arguments = ["--release"]
    if args.version:
        arguments += ["--version", args.version]
    if args.force:
        arguments.append("--force")
    return _delegate(INSTALL_SCRIPT, "update", arguments)


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Delegate to uninstall.sh, which owns the removal logic.

    Keeping every ``rm`` in one script means one place to audit, and it keeps
    working when the Python installation is the thing that is broken.
    """
    arguments = []
    if args.purge:
        arguments.append("--purge")
    if args.yes:
        arguments.append("--yes")
    return _delegate(UNINSTALL_SCRIPT, "uninstall", arguments)


def _delegate(script_name: str, command_name: str, arguments: List[str]) -> int:
    """Run one of aaDoctor's own shell scripts, which own the risky operations."""
    script = distribution_root() / script_name
    if not script.is_file():
        print(
            f"{script_name} not found in {distribution_root()}.\n"
            f"Run {script_name} from the release or checkout you installed from.",
            file=sys.stderr,
        )
        return EXIT_NOT_FOUND

    if not environment.is_root():
        print(f"'aadoctor {command_name}' requires root.", file=sys.stderr)
        return EXIT_PRIVILEGES

    command = ["/bin/bash", str(script)]
    command.extend(arguments)
    completed = subprocess.run(command, check=False)
    return completed.returncode


def cmd_daemon(args: argparse.Namespace) -> int:
    from .daemon import run as run_daemon

    try:
        config = load_config()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ENV_NOT_READY

    log_file = Path(args.log_file) if args.log_file else None
    return run_daemon(config, log_file=log_file, verbose=args.verbose)
