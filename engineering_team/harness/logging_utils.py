"""Logging utilities for the harness."""

from __future__ import annotations

import logging

from . import paths as _paths


def get_logger(name: str = "engineering_team") -> logging.Logger:
    _paths.ensure_workspace()
    logger = logging.getLogger(name)
    if logger.handlers and getattr(logger, "_harness_handler_path", None) == str(_paths.log_path()):
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Rebind the file handler when TEAM_ROOT moved (tests, scratch workspaces);
    # a cached handler would otherwise keep appending to the previous tree.
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            h.close()
    fh = logging.FileHandler(_paths.log_path(), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger._harness_handler_path = str(_paths.log_path())  # type: ignore[attr-defined]
    logger.propagate = False
    return logger
