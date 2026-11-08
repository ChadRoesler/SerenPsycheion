"""Entry point for `python -m seren_psycheion` or the `seren-psycheion` script.

--config / -c like the rest of the family; --describe answers before anything
heavy is imported, so a broken install can still say its own name.
"""
from __future__ import annotations

import argparse
import json
import sys


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _force_utf8_stdio()
    if "--describe" in sys.argv[1:]:
        from ._describe import DESCRIBE
        print(json.dumps(DESCRIBE))
        raise SystemExit(0)

    parser = argparse.ArgumentParser(prog="seren_psycheion",
                                     description="SerenPsycheion - the mind's workshop for a Seren body.")
    parser.add_argument("--config", "-c", default=None,
                        help="Path to seren-psycheion.yaml (default: $SEREN_PSYCHEION_CONFIG, then "
                             "~/seren-psycheion/seren-psycheion.yaml, then built-in defaults).")
    args = parser.parse_args()

    import uvicorn
    from seren_meninges.exposure import enforce_server

    from ._diag import diag
    from .app import create_app
    from .config import load_config

    cfg = load_config(args.config)
    # This page can switch a robot's lights and read what a hand did: an open bind
    # with no token is refused here, with the ways out printed.
    enforce_server(cfg.server, service="seren-psycheion", env_prefix="SEREN_PSYCHEION", log=diag)
    app = create_app(cfg)
    diag(f"[seren-psycheion] viewer at http://{cfg.host}:{cfg.port}/viewer")
    diag(f"[seren-psycheion] the body: robot {cfg.soma.robot.url}, voice {cfg.soma.voice.url}")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
