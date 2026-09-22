"""Filesystem paths owned or observed by aaDoctor.

Every absolute path the project uses is defined here exactly once. Nothing
outside ``OWNED_PATHS`` is ever created, modified or removed, and nothing under
``/www`` is ever written to.

See README.md section 5 and SPEC-001.
"""

from __future__ import annotations

from pathlib import Path

APP_NAME = "aadoctor"
DISPLAY_NAME = "aaDoctor"
SERVICE_NAME = "aadoctor.service"

# --- paths aaDoctor owns -------------------------------------------------

INSTALL_DIR = Path("/opt/aadoctor")
CONFIG_DIR = Path("/etc/aadoctor")
STATE_DIR = Path("/var/lib/aadoctor")
LOG_DIR = Path("/var/log/aadoctor")
CLI_PATH = Path("/usr/local/bin/aadoctor")
SERVICE_UNIT = Path("/etc/systemd/system/aadoctor.service")

CONFIG_FILE = CONFIG_DIR / "config.toml"
VERSION_FILE = INSTALL_DIR / "VERSION"
LOG_FILE = LOG_DIR / "aadoctor.log"
STATE_FILE = STATE_DIR / "state.json"
#: Published by the daemon for the CLI to read; metrics, not offsets, and the
#: two are kept apart because they have different lifetimes and owners.
RUNTIME_FILE = STATE_DIR / "runtime.json"
OFFSETS_DIR = STATE_DIR / "offsets"
INCIDENTS_DIR = STATE_DIR / "incidents"

#: Everything aaDoctor may create. Anything else is out of bounds.
OWNED_PATHS = (
    INSTALL_DIR,
    CONFIG_DIR,
    STATE_DIR,
    LOG_DIR,
    CLI_PATH,
    SERVICE_UNIT,
)

#: Removed by a plain uninstall. Configuration and state are preserved.
UNINSTALL_PATHS = (
    SERVICE_UNIT,
    CLI_PATH,
    INSTALL_DIR,
)

#: Removed by ``uninstall --purge`` (README section 54).
PURGE_PATHS = (
    SERVICE_UNIT,
    CLI_PATH,
    INSTALL_DIR,
    CONFIG_DIR,
    STATE_DIR,
    LOG_DIR,
)

# --- paths aaDoctor only reads -------------------------------------------

AAPANEL_DIR = Path("/www/server/panel")
NGINX_VHOST_DIR = Path("/www/server/panel/vhost/nginx")
WWWLOGS_DIR = Path("/www/wwwlogs")

#: Never written to, under any circumstance (README section 5).
READ_ONLY_PREFIXES = (
    Path("/www/server"),
    Path("/www/wwwroot"),
    Path("/www/wwwlogs"),
)


class UnsafePathError(RuntimeError):
    """Raised when a path outside aaDoctor's own tree would be modified."""


def is_owned(path: Path) -> bool:
    """True when ``path`` is one of aaDoctor's own paths."""
    return Path(path) in OWNED_PATHS


def is_read_only_area(path: Path) -> bool:
    """True when ``path`` lives under an area aaDoctor must never write to."""
    candidate = Path(path)
    for prefix in READ_ONLY_PREFIXES:
        if candidate == prefix or prefix in candidate.parents:
            return True
    return False


def assert_removable(path: Path) -> Path:
    """Return ``path`` if aaDoctor is allowed to remove it, else raise.

    The guard is deliberately an exact allowlist rather than a prefix check: a
    bug that produces an empty or unexpected path must fail loudly instead of
    removing something broader.
    """
    candidate = Path(path)
    if candidate not in PURGE_PATHS:
        raise UnsafePathError(f"refusing to remove path not owned by aaDoctor: {candidate}")
    return candidate


def package_root() -> Path:
    """Directory holding the ``aadoctor`` package."""
    return Path(__file__).resolve().parent


def distribution_root() -> Path:
    """Root of the running distribution.

    ``/opt/aadoctor`` once installed, the repository checkout during
    development. Derived from the package location so both work unchanged.
    """
    return package_root().parent.parent


def resolve_version() -> str:
    """Read the version from the single VERSION file, if it can be found."""
    candidates = (distribution_root() / "VERSION", VERSION_FILE)
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0-unknown"
