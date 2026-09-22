"""aaDoctor - lightweight, deterministic, non-invasive aaPanel diagnostics.

Read README.md before changing anything here. aaDoctor observes; it never
modifies aaPanel, Nginx, PHP or any service other than its own.
"""

from __future__ import annotations

from .paths import DISPLAY_NAME, resolve_version

#: Lowest Python this project supports. See ADR-008.
MINIMUM_PYTHON = (3, 8)

__version__ = resolve_version()

__all__ = ["DISPLAY_NAME", "MINIMUM_PYTHON", "__version__"]
