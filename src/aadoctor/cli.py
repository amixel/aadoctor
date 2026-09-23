"""aaDoctor command line interface.

Only commands that actually do something are registered. The one planned
command left (``explain``) is deliberately absent rather than present as a
stub, so nothing can look implemented when it is not. See SPEC-008 for the
full intended surface.

``show`` and ``diagnose`` are kept apart on purpose: one renders the facts an
incident recorded, the other renders a reading of them. Anyone can check the
second against the first.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from . import discovery, environment, rules, runtime, service, storage
from .analyzers import diagnosis, incidents
from .config import ConfigError, load as load_config
from .paths import (
    CONFIG_FILE,
    DISPLAY_NAME,
    INCIDENTS_DIR,
    INSTALL_DIR,
    RUNTIME_FILE,
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

    top = commands.add_parser("top", help="what the recent traffic looks like")
    top.add_argument(
        "--window",
        default="5m",
        metavar="1m|5m",
        help="how far back to look (default: 5m)",
    )
    top.add_argument("--site", metavar="NAME", help="one site in detail")
    top.add_argument("--json", action="store_true", help="machine-readable output")

    listing = commands.add_parser("incidents", help="periods of elevated load")
    listing.add_argument(
        "--limit", type=int, default=20, metavar="N", help="how many to show"
    )
    listing.add_argument("--json", action="store_true", help="machine-readable output")

    show = commands.add_parser("show", help="one incident in detail")
    show.add_argument("incident", metavar="ID", help="incident id")
    show.add_argument("--json", action="store_true", help="machine-readable output")

    diagnosing = commands.add_parser(
        "diagnose", help="what the evidence says about an incident"
    )
    diagnosing.add_argument(
        "incident",
        metavar="ID",
        nargs="?",
        help="incident id (default: the most recent one)",
    )
    diagnosing.add_argument(
        "--json", action="store_true", help="machine-readable output"
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
        "top": cmd_top,
        "incidents": cmd_incidents,
        "show": cmd_show,
        "diagnose": cmd_diagnose,
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

    _print_runtime_status()

    print("Monitoring:")
    print("following logs, counting traffic and watching the load")
    print()
    print("Diagnosis:")
    print("run `aadoctor diagnose` on a recorded incident")

    # Exit 0 whether or not the daemon is running. `status` exists to report
    # state, and a report that fails when there is nothing to report is a
    # report nobody can script around.
    return EXIT_OK


def _print_runtime_status() -> None:
    """Load and open incident, from what the daemon last published."""
    payload = runtime.read()
    if payload is None:
        return

    load = payload.get("load")
    if isinstance(load, dict):
        print("Current load:")
        print(
            f"{float(load.get('load1', 0)):.2f} over "
            f"{int(load.get('cpu_count', 1))} CPUs "
            f"({float(load.get('load_per_cpu', 0)):.2f} per core)"
        )
        print()

    incident = payload.get("incident")
    print("Incident:")
    if isinstance(incident, dict) and incident.get("open"):
        print(
            f"OPEN since {incident.get('started_at', '-')}, "
            f"peak {float(incident.get('peak_load_per_cpu', 0)):.2f} per core"
        )
    else:
        print("none open")
    print()


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


#: A snapshot older than this is called out rather than shown as current.
STALE_AFTER = 45.0

#: Above this share of unparsed access lines, the traffic figures describe
#: only part of what the server did, and saying so matters more than the table.
INCOMPLETE_RATIO = 0.10


def cmd_top(args: argparse.Namespace) -> int:
    """Show recent traffic. Describes what happened; concludes nothing."""
    seconds = _window_seconds(args.window)
    if seconds is None:
        print(f"unknown window: {args.window}. Use 1m or 5m.", file=sys.stderr)
        return EXIT_USAGE

    payload = runtime.read()
    if payload is None:
        print(
            f"No traffic data yet. The daemon publishes it to {RUNTIME_FILE}\n"
            f"once it is running: {_daemon_hint()}",
            file=sys.stderr,
        )
        return EXIT_DAEMON_NOT_RUNNING

    window = runtime.window(payload, seconds)
    if window is None:
        available = ", ".join(f"{value}s" for value in runtime.available_windows(payload))
        print(f"no {seconds}s window in the snapshot; it has: {available}", file=sys.stderr)
        return EXIT_NOT_FOUND

    if args.site:
        site = _find_site(window, args.site)
        if site is None:
            print(f"{args.site} had no traffic in the last {seconds}s.", file=sys.stderr)
            return EXIT_NOT_FOUND
        if args.json:
            print(json.dumps(site, indent=2, sort_keys=True))
            return EXIT_OK
        _print_header(payload, seconds)
        _print_site(site)
        return EXIT_OK

    if args.json:
        print(json.dumps(window, indent=2, sort_keys=True))
        return EXIT_OK

    _print_header(payload, seconds)
    _print_window(window)
    return EXIT_OK


def _window_seconds(value: str) -> Optional[int]:
    """Accept 1m, 5m, 60 or 300. Deliberately not a duration parser."""
    text = str(value).strip().lower()
    known = {"1m": 60, "60": 60, "60s": 60, "5m": 300, "300": 300, "300s": 300}
    return known.get(text)


def _daemon_hint() -> str:
    if not service.available():
        return "run 'aadoctor daemon' or install the service"
    state = service.is_active()
    if state == "active":
        return "the daemon is running but has not published yet"
    return "start it with 'aadoctor enable'"


def _print_header(payload: dict, seconds: int) -> None:
    age = runtime.age(payload)
    label = "last 5 minutes" if seconds == 300 else f"last {seconds} seconds"
    print(f"{DISPLAY_NAME} - {label}")

    if age is None:
        print("Snapshot age unknown.")
    elif age > STALE_AFTER:
        # Never pass an old snapshot off as current.
        print(f"Snapshot is {_age_text(age)} old - {_daemon_hint()}.")
    else:
        print(f"Updated {_age_text(age)} ago.")

    load = payload.get("load")
    if isinstance(load, dict):
        print(
            f"Load: {float(load.get('load1', 0)):.2f} over "
            f"{int(load.get('cpu_count', 1))} CPUs "
            f"({float(load.get('load_per_cpu', 0)):.2f} per core)"
        )

    incident = payload.get("incident")
    if isinstance(incident, dict) and incident.get("open"):
        print(f"An incident is open: {incident.get('id', '-')}")
    print()


def _age_text(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h{int((seconds % 3600) // 60):02d}m"


def _print_window(window: dict) -> None:
    total = int(window.get("total_requests", 0))
    print(f"Requests: {total:,}")
    print(f"Average:  {window.get('requests_per_second', 0)} req/s")

    _print_incomplete_warning(window, total)

    if not total and not window.get("total_errors"):
        print()
        print("No traffic in this window.")
        return

    _print_entries("TOP SITES", window.get("sites"), share=True)
    _print_site_paths(window)
    _print_entries("TOP IPS", window.get("ips"), share=True)
    _print_map("STATUS", window.get("status_classes"), order=sorted)
    _print_map("ERRORS", window.get("error_kinds"))

    other = {key: value for key, value in (window.get("other") or {}).items() if value}
    if other:
        print()
        print("Not shown individually (cardinality limit reached):")
        for name, value in sorted(other.items()):
            print(f"  {name}: {value:,} requests in less frequent keys")


def _print_incomplete_warning(window: dict, total: int) -> None:
    unparsed = int(window.get("access_unparsed", 0))
    seen = total + unparsed
    if not seen or unparsed / float(seen) < INCOMPLETE_RATIO:
        return

    share = 100.0 * unparsed / seen
    print()
    print(f"WARNING: {share:.0f}% of access lines were not parsed ({unparsed:,} lines).")
    print("Traffic below is incomplete. Run 'aadoctor doctor' for details.")


#: SPEC-008: the report is read over SSH, often on a tethered phone.
LINE_WIDTH = 80
COUNT_WIDTH = 9
SHARE_WIDTH = 7
MIN_KEY_WIDTH = 20
MAX_SITE_WIDTH = 24


def _fit(value: str, width: int) -> str:
    """Shorten to `width`, cutting the middle rather than the end.

    Real paths are long slugs, and they differ at the end as often as at the
    start: `/info/1487279872.html` and `/info/441747584.html` share a prefix,
    and two articles on the same site share a long one. Cutting the tail would
    render distinct rows identically, which is worse than not showing them.
    """
    if len(value) <= width:
        return value
    if width <= len(_ELLIPSIS):
        return value[:width]

    keep = width - len(_ELLIPSIS)
    head = (keep + 1) // 2
    return value[:head] + _ELLIPSIS + value[len(value) - (keep - head):]


#: Plain ASCII, so the output survives being piped, copied out of a terminal
#: or pasted into a ticket.
_ELLIPSIS = "..."


def _print_entries(title: str, entries, share: bool = False) -> None:
    if not entries:
        return
    print()
    print(title)

    budget = LINE_WIDTH - COUNT_WIDTH - 2 - (SHARE_WIDTH if share else 0)
    width = min(
        max([len(str(entry.get("key", ""))) for entry in entries] + [MIN_KEY_WIDTH]),
        budget,
    )
    for entry in entries:
        key = _fit(str(entry.get("key", "")), width)
        line = f"{key.ljust(width)}  {int(entry.get('count', 0)):>9,}"
        if share:
            line += f"  {100.0 * float(entry.get('share', 0.0)):>5.1f}%"
        print(line)


def _print_site_paths(window: dict) -> None:
    """Paths, with the site they belong to - a path alone is not actionable."""
    rows = []
    for site in window.get("per_site") or []:
        for entry in site.get("paths") or []:
            rows.append((site.get("name", "-"), entry.get("key", "-"), int(entry.get("count", 0))))
    if not rows:
        return

    rows.sort(key=lambda row: (-row[2], row[0], row[1]))
    rows = rows[:10]

    print()
    print("TOP PATHS")
    # Both columns are bounded. A real server's paths run to a hundred
    # characters, and letting the column grow to the longest one pushed this
    # table far past the width of anyone's terminal.
    site_width = min(max(len(row[0]) for row in rows), MAX_SITE_WIDTH)
    path_width = max(MIN_KEY_WIDTH, LINE_WIDTH - site_width - COUNT_WIDTH - 4)

    for site, path, count in rows:
        print(
            f"{_fit(site, site_width).ljust(site_width)}  "
            f"{_fit(path, path_width).ljust(path_width)}  "
            f"{count:>9,}"
        )


def _print_map(title: str, values, order=None) -> None:
    if not values:
        return
    print()
    print(title)
    items = list(values.items())
    items = order(items) if order else sorted(items, key=lambda item: (-item[1], item[0]))
    for key, value in items:
        print(f"{str(key).ljust(20)}  {int(value):>9,}")


def _find_site(window: dict, name: str) -> Optional[dict]:
    for site in window.get("per_site") or []:
        if site.get("name") == name:
            return site
    return None


def _print_site(site: dict) -> None:
    print(f"SITE: {site.get('name')}")
    print()
    print(f"Requests: {int(site.get('requests', 0)):,}")
    print(f"Share:    {100.0 * float(site.get('share', 0.0)):.1f}% of the server")
    if site.get("unparsed"):
        print(f"Unparsed: {int(site['unparsed']):,} lines")

    _print_entries("TOP PATHS", site.get("paths"))
    _print_entries("TOP IPS", site.get("ips"))
    _print_map("STATUS", site.get("statuses"))
    _print_map("ERRORS", site.get("error_kinds"))


def cmd_incidents(args: argparse.Namespace) -> int:
    """List periods of elevated load. Says when, not why."""
    limit = max(1, int(args.limit))
    found = incidents.load_incidents(limit=limit)

    if args.json:
        print(json.dumps([item.as_dict() for item in found], indent=2, sort_keys=True))
        return EXIT_OK

    if not found:
        print("No incidents recorded.")
        print(f"The daemon writes them to {INCIDENTS_DIR} when load rises.")
        return EXIT_OK

    print(f"{'ID':<21} {'START':<15} {'DURATION':>11}  {'PEAK/CORE':>9}  SEVERITY")
    for item in found:
        print(
            f"{item.id:<21} "
            f"{_short_time(item.started_at):<15} "
            f"{_duration_text(item):>11}  "
            f"{item.peak_load_per_cpu:>9.2f}  "
            f"{item.severity}"
        )
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    """Render one incident: the load, and the traffic that accompanied it."""
    incident = incidents.load_incident(args.incident)
    if incident is None:
        print(f"No incident {args.incident} in {INCIDENTS_DIR}", file=sys.stderr)
        return EXIT_NOT_FOUND

    if args.json:
        print(json.dumps(incident.as_dict(), indent=2, sort_keys=True))
        return EXIT_OK

    print(f"INCIDENT {incident.id}")
    print(f"Status:   {incident.status.upper()}")
    print(f"Severity: {incident.severity}")
    print(f"Started:  {incident.started_at}")
    print(f"Peak:     {incident.peak_at or '-'}")
    print(f"Ended:    {incident.ended_at or '-'}")
    if incident.status == incidents.INTERRUPTED:
        print("The daemon stopped while this incident was open; its end is unknown.")
    duration = incident.duration_seconds()
    if duration is not None:
        print(f"Duration: {duration:.0f}s")

    print()
    print("SERVER")
    print(f"CPUs:          {incident.cpu_count}"
          + (" (assumed)" if incident.cpus_assumed else ""))
    _print_load("Start", incident.start_load)
    _print_load("Peak", incident.peak_load)

    traffic = incident.traffic_at_peak or incident.traffic_at_start
    if not isinstance(traffic, dict):
        print()
        print("No traffic was recorded with this incident.")
        return EXIT_OK

    label = "at the peak" if incident.traffic_at_peak else "when it opened"
    window = traffic.get(str(runtime.WINDOWS[-1])) or traffic.get(str(runtime.WINDOWS[0]))
    if not isinstance(window, dict):
        print()
        print("No traffic was recorded with this incident.")
        return EXIT_OK

    print()
    print(f"TRAFFIC {label}")
    _print_coverage(window)
    _print_window(window)
    return EXIT_OK


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Say what the evidence supports. The only command that interprets.

    `show` renders facts; this renders a reading of them. Keeping the two
    commands apart is what lets someone check the conclusion against the
    numbers it came from.
    """
    incident = _incident_to_diagnose(args.incident)
    if incident is None:
        if args.incident:
            print(f"No incident {args.incident} in {INCIDENTS_DIR}", file=sys.stderr)
            return EXIT_NOT_FOUND
        print("No incidents recorded.")
        print(f"The daemon writes them to {INCIDENTS_DIR} when load rises.")
        return EXIT_OK

    try:
        thresholds = rules.Thresholds.from_config(load_config())
    except ConfigError:
        # A broken configuration must not stop a diagnosis: the defaults are
        # documented, and saying so is better than refusing to answer.
        thresholds = rules.Thresholds()
        print("Configuration could not be read; using default thresholds.", file=sys.stderr)

    result = diagnosis.diagnose(incident, thresholds)

    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return EXIT_OK

    _print_diagnosis(result)
    return EXIT_OK


def _incident_to_diagnose(incident_id: Optional[str]):
    """The incident asked for, or the most recent one."""
    if incident_id:
        return incidents.load_incident(incident_id)
    found = incidents.load_incidents(limit=1)
    return found[0] if found else None


def _print_diagnosis(result: "diagnosis.Diagnosis") -> None:
    facts = result.facts
    print(f"{DISPLAY_NAME} Diagnosis")
    print()
    print(f"Incident:  {result.incident_id}  ({result.status.upper()}, {result.severity})")

    if facts is not None:
        assumed = " (assumed)" if facts.cpus_assumed else ""
        print(
            f"Peak load: {facts.peak_load:.2f} over {facts.cpu_count} CPUs{assumed}"
            f" - {facts.peak_load_per_cpu:.2f} per core"
        )
        print(
            f"Window:    {facts.window_seconds}s at the {facts.source or 'peak'}, "
            f"{facts.coverage_seconds:.0f}s of data, "
            f"{facts.total_requests:,} requests"
        )

    if result.primary_site:
        print()
        print("PRIMARY SITE")
        print(result.primary_site)

    if result.primary_path:
        print()
        print("PRIMARY PATH")
        # The aggregate is the path without its query string (SPEC-005), so
        # calling this a URL would promise more than the data holds.
        print(result.primary_path)

    if result.associated_ip:
        print()
        print("ASSOCIATED IP")
        print(result.associated_ip)
        _wrap(
            "high request concentration from one address; it may be a CDN, "
            "proxy, NAT, integration or crawler rather than an attack"
        )

    if result.error_site:
        print()
        print("ERROR CONCENTRATION")
        print(result.error_site)

    if result.traffic_site:
        print()
        print("TRAFFIC CONCENTRATION")
        print(result.traffic_site)

    evidence = result.evidence()
    if evidence:
        print()
        print("EVIDENCE")
        for line in evidence:
            print(f"- {line}")

    if result.findings:
        print()
        print("FINDINGS")
        for finding in result.findings:
            print(f"{finding.level.replace('_', ' '):<10} {finding.code}")

    if result.not_evaluable:
        print()
        print("NOT EVALUABLE")
        for item in result.not_evaluable:
            print(f"{item.code:<22} {item.reason.replace('_', ' ')}")

    issues = result.quality.issues() if result.quality else []
    if issues:
        print()
        print("DATA QUALITY")
        for line in issues:
            print(f"- {line}")

    print()
    print("CONFIDENCE")
    print(result.level.replace("_", " ") if result.conclusive else "-")
    print()
    _wrap(result.summary)
    print()
    _wrap(
        "Evidence read from web server logs. A cause outside them - I/O, a "
        "backup, a remote database, a process owned by no site - cannot "
        "appear here."
    )


def _wrap(text: str, width: int = 78) -> None:
    """Print prose inside the terminal, not through the right-hand edge.

    The report is read over SSH during an incident, often on a phone. A line
    that wraps in the middle of a site name is a line nobody reads.
    """
    for line in textwrap.wrap(text, width=width) or [""]:
        print(line)


def _print_load(label: str, values: dict) -> None:
    if not values:
        return
    print(
        f"{label + ':':<15}"
        f"load1 {float(values.get('load1', 0)):>6.2f}   "
        f"load5 {float(values.get('load5', 0)):>6.2f}   "
        f"load15 {float(values.get('load15', 0)):>6.2f}   "
        f"per core {float(values.get('load_per_cpu', 0)):>5.2f}"
    )


def _print_coverage(window: dict) -> None:
    """Say how much of the window actually has data behind it.

    A window that has only been collecting for forty seconds must not be
    presented as five minutes of evidence.
    """
    requested = int(window.get("window_seconds", 0))
    covered = float(window.get("coverage_seconds", requested))

    if covered and covered < requested - 1:
        print(f"Window: {requested}s requested, {covered:.0f}s of data available")
    else:
        print(f"Window: {requested}s")


def _short_time(value: str) -> str:
    """The clock part of an ISO timestamp, for a table."""
    if not value:
        return "-"
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return value[:15]
    return stamp.strftime("%d/%m %H:%M:%S")


def _duration_text(incident) -> str:
    """How long it ran, or why that is not a number.

    An interrupted incident is named rather than shown as a bare dash. SPEC-006
    created that status so a daemon restart mid-incident would be visible, and
    a dash in a duration column reads as "could not compute" - which hides the
    one thing worth knowing about that row.
    """
    if incident.status == incidents.OPEN:
        return "open"
    if incident.status == incidents.INTERRUPTED:
        return "interrupted"
    seconds = incident.duration_seconds()
    if seconds is None:
        return "-"
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f}m"


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
