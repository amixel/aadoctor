"""Control of aaDoctor's own systemd service - and nothing else.

Every call in this module appends the literal ``aadoctor.service``. The unit
name is never taken from an argument, a configuration value or user input, so
there is no path by which aaDoctor can act on Nginx, PHP-FPM, MySQL or any
other unit (README.md section 4, ADR-001, ADR-004).
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Sequence

from .paths import SERVICE_NAME

SYSTEMCTL = "systemctl"


class ServiceError(RuntimeError):
    """systemd is unavailable or a systemctl call failed."""


def available() -> bool:
    return shutil.which(SYSTEMCTL) is not None


def _run(arguments: Sequence[str], unit: bool = True) -> subprocess.CompletedProcess:
    if not available():
        raise ServiceError("systemctl not found; systemd is required")

    command: List[str] = [SYSTEMCTL]
    command.extend(arguments)
    if unit:
        # The only unit this project ever touches.
        command.append(SERVICE_NAME)

    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    )


def _state(argument: str) -> str:
    """Return the raw first line of ``systemctl is-active|is-enabled``.

    A non-zero exit is expected for an inactive or disabled unit, so the exit
    code is deliberately ignored in favour of the printed state.
    """
    result = _run([argument])
    text = (result.stdout or "").strip()
    if text:
        return text.splitlines()[0]
    return "unknown"


def is_active() -> str:
    return _state("is-active")


def is_enabled() -> str:
    return _state("is-enabled")


def is_installed() -> bool:
    """True when the unit file is known to systemd."""
    return is_enabled() != "not-found"


def enable() -> subprocess.CompletedProcess:
    """``systemctl enable --now aadoctor.service`` - idempotent by design."""
    return _run(["enable", "--now"])


def disable() -> subprocess.CompletedProcess:
    """``systemctl disable --now aadoctor.service`` - idempotent by design."""
    return _run(["disable", "--now"])


def daemon_reload() -> subprocess.CompletedProcess:
    return _run(["daemon-reload"], unit=False)


def failure_text(result: subprocess.CompletedProcess) -> str:
    """Best available explanation for a failed systemctl call."""
    for stream in (result.stderr, result.stdout):
        text = (stream or "").strip()
        if text:
            return text
    return f"systemctl exited with status {result.returncode}"
