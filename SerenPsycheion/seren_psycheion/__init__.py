"""seren_psycheion - the mind's workshop for a Seren body.

SerenSoma is the body: a Stack-chan robot's firmware and the voice server that
is its nervous system. Psycheion is where you watch it and shape it: what it
senses and what it was read as, how bright and loud it is, when it's night,
and (moving in from SerenSoma) the faces it wears.

It talks to the body only through seren_sinew.soma, and it holds the body's
tokens itself: the browser only ever holds Psycheion's own.
"""
from __future__ import annotations

try:  # the setuptools-scm build artifact (gitignored); present in installed wheels
    from ._version import version as __version__
except Exception:  # source checkout without a build -> metadata, then dev fallback
    try:
        from importlib.metadata import version as _v

        __version__ = _v("seren-psycheion")
    except Exception:
        __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
