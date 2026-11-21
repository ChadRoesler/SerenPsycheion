"""Startup diagnostics that survive a nulled stdout (parity with SerenTheatre's
and SerenMargin's _diag): `pythonw` nulls sys.stdout, and a write to a closed
stream would end the run. Print, but never let printing be what kills it."""
from __future__ import annotations

import logging
import sys


def log_what_the_service_does() -> logging.Logger:
    """Give the package's own loggers somewhere to go, at INFO. With no handler, Python
    drops everything under WARNING and prints warnings bare: a build-up that fired left
    no line, only its failed steps did, with no time on them. The format is the request
    log's, so the two read as one log. Safe to call twice."""
    logger = logging.getLogger("seren_psycheion")
    if not any(getattr(h, "_seren_psycheion", False) for h in logger.handlers):
        handler = logging.StreamHandler()          # stderr: the journal, under systemd
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        handler._seren_psycheion = True            # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False                       # not again through the root, if something sets one up
    return logger


def diag(message: str) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None:
                continue
            stream.write(message + "\n")
            stream.flush()
            return
        except Exception:  # noqa: BLE001 - a broken stream must not be fatal
            continue
