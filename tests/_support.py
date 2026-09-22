"""Test bootstrap: put src/ on sys.path without installing anything.

Run the suite with:

    python3 -m unittest discover -s tests -t tests
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
