"""Shared test configuration and fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from amz_download import log

# Make the local test helpers (e.g. fake_amazon) importable.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def info_logging():
    """Run with the package logger at INFO, then restore quiet."""
    log.configure(1)
    yield
    log.configure(0)


@pytest.fixture
def debug_logging():
    """Run with the package logger at DEBUG, then restore quiet."""
    log.configure(2)
    yield
    log.configure(0)
