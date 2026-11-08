"""SerenPsycheion's identity card. STDLIB ONLY - nothing imported, nothing read.

What an INSTALLED Psycheion answers to `--describe`, so it can be compared with
its Starwright card once that exists. Standard library only, for the reason
every Seren card is: the moment you most want a thing to say its own name is
when its install is broken.
"""
from __future__ import annotations

NAME = "seren-psycheion"
DISPLAY = "Seren Psycheion"
DESCRIPTION = ("The mind's workshop for a Seren body: watch what it senses and "
               "does, set how it behaves, and make its faces.")

# 7429: between Lyceum (7428) and Probe (7430). The Soma voice server is 7480
# and the robot's own control API 8080; neither is in the family band.
PORT = 7429

GROUP = "auxiliary"

# Orchid: psyche is the butterfly.
ACCENT = "#f2aeb1"

# Nothing to install first. A body is reached over its own HTTP APIs, so a
# Psycheion with no body configured still serves its pages and says so.
REQUIRES: tuple = ()
EXTRAS: tuple = ()

DESCRIBE = {
    "schema_version": 1,
    "name": NAME,
    "display": DISPLAY,
    "description": DESCRIPTION,
    "port": PORT,
    "group": GROUP,
    "accent": ACCENT,
    "package": "seren-psycheion",
    "requires": list(REQUIRES),
    "extras": list(EXTRAS),
}
