"""Startup diagnostics that survive a nulled stdout (parity with SerenTheatre's
and SerenMargin's _diag): `pythonw` nulls sys.stdout, and a write to a closed
stream would end the run. Print, but never let printing be what kills it."""
from __future__ import annotations

import sys


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
