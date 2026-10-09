"""Logging setup for the package: one handler on stderr, quiet unless asked.

Verbosity counts the `-v` flags the user passed: none keeps the current
behaviour (warnings only), once adds the run narrative, and twice or more adds
per-request detail.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

PACKAGE = "amz_download"

LEVELS = {1: logging.INFO, 2: logging.DEBUG}
DEFAULT_LEVEL = logging.WARNING


def level_for(verbose: int) -> int:
    """Map a `-v` count to a logging level; 0 keeps warnings visible only."""
    if verbose <= 0:
        return DEFAULT_LEVEL
    return LEVELS.get(verbose, logging.DEBUG)


def configure(verbose: int) -> None:
    """Set the package logger's level and attach one stderr handler.

    Calling this more than once is safe: the level is updated each time and the
    handler is attached only the first time. Propagation is deliberately left
    on so that pytest's `caplog` can observe records.
    """
    logger = logging.getLogger(PACKAGE)
    logger.setLevel(level_for(verbose))
    if logger.handlers:
        return

    handler = RichHandler(
        console=Console(stderr=True),
        markup=False,
        rich_tracebacks=False,
        show_time=False,
        show_path=False,
    )
    if logger.level <= logging.INFO:
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    logger.addHandler(handler)
