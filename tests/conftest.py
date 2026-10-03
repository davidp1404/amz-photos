"""Shared test configuration."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the local test helpers (e.g. fake_amazon) importable.
sys.path.insert(0, str(Path(__file__).parent))
