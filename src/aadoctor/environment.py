"""Environment detection.

Every function here is read-only. Nothing is created, changed or fixed - if a
check fails, the report says so and the administrator decides what to do
(README.md section 4, ADR-001).

Checks take a ``root`` so they can run against a fixture tree in tests without
any abstraction layer: pass a temporary directory instead of ``/``.

Detection stops at "does this exist and can I read it". Parsing vhosts belongs
to SPEC-002 and is not implemented yet.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import MINIMUM_PYTHON

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

#: Paths checked, relative to ``root`` so tests can point them elsewhere.
AAPANEL_RELATIVE = "www/server/panel"
NGINX_VHOST_RELATIVE = "www/server/panel/vhost/nginx"
WWWLOGS_RELATIVE = "www/wwwlogs"
SYSTEMD_RUNTIME_RELATIVE = "run/systemd/system"


class Check:
    """One environment check and its outcome."""

    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name = name
        self.status = status
        self.detail = detail

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Check({self.name!r}, {self.status!r}, {self.detail!r})"


def is_root() -> bool:
    """True when running with effective uid 0.

    ``os.geteuid`` does not exist on every platform, so the CLI can still be
    inspected on a developer machine that is not Linux.
    """
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    return geteuid() == 0


def is_linux() -> bool:
    return platform.system() == "Linux"


def python_version_text() -> str:
    return "{0}.{1}.{2}".format(*sys.version_info[:3])


def has_systemd(root: Optional[Path] = None) -> bool:
    """True when this host is running systemd."""
    base = Path(root) if root is not None else Path("/")
    if (base / SYSTEMD_RUNTIME_RELATIVE).is_dir():
        return True
    # A fixture root has no /run/systemd; fall back to the binary only for the
    # real filesystem, so tests stay deterministic.
    if root is None:
        return shutil.which("systemctl") is not None
    return False


def aapanel_dir(root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else Path("/")
    return base / AAPANEL_RELATIVE


def nginx_vhost_dir(root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else Path("/")
    return base / NGINX_VHOST_RELATIVE


def wwwlogs_dir(root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else Path("/")
    return base / WWWLOGS_RELATIVE


def has_aapanel(root: Optional[Path] = None) -> bool:
    return aapanel_dir(root).is_dir()


def _readable(path: Path) -> bool:
    return os.access(str(path), os.R_OK)


def check_all(root: Optional[Path] = None) -> List[Check]:
    """Run every environment check and return the results in report order."""
    checks: List[Check] = []

    if is_linux():
        checks.append(Check("Linux", OK, platform.release()))
    else:
        checks.append(
            Check(
                "Linux",
                FAIL,
                f"{platform.system() or 'unknown system'} detected; aaDoctor targets Linux",
            )
        )

    minimum = "{0}.{1}".format(*MINIMUM_PYTHON)
    if sys.version_info >= MINIMUM_PYTHON:
        checks.append(Check("Python 3", OK, python_version_text()))
    else:
        checks.append(
            Check("Python 3", FAIL, f"{python_version_text()} found, {minimum} or newer required")
        )

    panel = aapanel_dir(root)
    if panel.is_dir():
        checks.append(Check("aaPanel", OK, str(panel)))
    else:
        checks.append(Check("aaPanel", FAIL, f"not found at {panel}"))

    vhosts = nginx_vhost_dir(root)
    if vhosts.is_dir():
        detail = str(vhosts) if _readable(vhosts) else f"{vhosts} (not readable)"
        status = OK if _readable(vhosts) else FAIL
        checks.append(Check("Nginx vhost directory", status, detail))
    else:
        checks.append(
            Check(
                "Nginx vhost directory",
                FAIL,
                f"not found at {vhosts}; aaDoctor supports aaPanel + Nginx only",
            )
        )

    logs = wwwlogs_dir(root)
    if logs.is_dir():
        if _readable(logs):
            checks.append(Check("Site logs", OK, str(logs)))
        else:
            checks.append(Check("Site logs", FAIL, f"{logs} is not readable"))
    else:
        checks.append(Check("Site logs", WARN, f"not found at {logs}"))

    if has_systemd(root):
        checks.append(Check("systemd", OK, ""))
    else:
        checks.append(Check("systemd", FAIL, "systemd is required to run the daemon"))

    if is_root():
        checks.append(Check("Privileges", OK, "running as root"))
    else:
        checks.append(
            Check(
                "Privileges",
                WARN,
                "not running as root; some site logs may be unreadable",
            )
        )

    return checks


def ready(checks: List[Check]) -> bool:
    """True when no check failed. Warnings do not hide a failure."""
    return not any(check.failed for check in checks)
