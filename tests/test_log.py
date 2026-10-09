"""Tests for the logging setup module."""

from __future__ import annotations

import logging

from rich.logging import RichHandler

from amz_download import log


def logger() -> logging.Logger:
    return logging.getLogger(log.PACKAGE)


def test_level_mapping():
    expected = {
        0: logging.WARNING,
        1: logging.INFO,
        2: logging.DEBUG,
        3: logging.DEBUG,
    }
    for verbose, level in expected.items():
        assert log.level_for(verbose) == level
        log.configure(verbose)
        assert logger().level == level
        assert logger().isEnabledFor(level)


def test_handler_attached_once():
    log.configure(1)
    log.configure(2)
    handlers = logger().handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0], RichHandler)


def test_handler_does_not_interpret_markup():
    log.configure(2)
    record = logging.LogRecord(
        log.PACKAGE,
        logging.DEBUG,
        __file__,
        1,
        "[bracketed] [red]name[/red]",
        None,
        None,
    )
    handler = logger().handlers[0]
    handler.handle(record)  # renders to stderr; must not eat the brackets
    assert handler.markup is False


def test_default_level_emits_no_info_records(caplog):
    log.configure(0)
    client_logger = logging.getLogger("amz_download.client")
    assert client_logger.isEnabledFor(logging.INFO) is False
    client_logger.info("narrative that must not appear")
    client_logger.warning("warning that must appear")
    assert "narrative that must not appear" not in caplog.text
    assert "warning that must appear" in caplog.text


def test_records_survive_markup_parsing(capsys):
    log.configure(2)
    logging.getLogger("amz_download.client").debug("node [odd] name [not markup]")
    captured = capsys.readouterr()
    assert "[odd]" in captured.err
    assert "[not markup]" in captured.err


def test_verbose_names_are_stable():
    assert log.PACKAGE == "amz_download"
