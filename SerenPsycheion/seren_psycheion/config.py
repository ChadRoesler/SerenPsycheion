"""SerenPsycheion's config: dataclasses on the shared Meninges blocks, like Theatre and Probe.

Where it's read from, first found wins:

    --config  ->  $SEREN_PSYCHEION_CONFIG  ->  ~/seren-psycheion/seren-psycheion.yaml  ->  defaults

THE BODY'S TOKENS ARE POINTERS, never secrets in this file unless you choose the
escape hatch: `token_env` (an environment variable's name), `token_keyring`
("service/username" in the OS keychain), or `token_file` (a file holding only the
token - SerenSoma keeps its own in its state folder, ~/seren-soma/state/, and pointing at those
is the simplest setup on the box the body's voice server runs on). The browser
never sees them: Psycheion makes the calls itself.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from seren_meninges import ServerConfig, resolve_token

from ._describe import PORT as DEFAULT_PORT
from ._diag import diag

try:
    import yaml
    _HAS_YAML = True
except ImportError:  # pragma: no cover - pyyaml is a dependency
    _HAS_YAML = False

# Loopback unless the yaml says otherwise: this page can turn a robot's lights
# off and read what a hand did. Widening it is something you do on purpose.
DEFAULT_HOST = "127.0.0.1"


@dataclass
class Endpoint:
    """One side of the body: where it is, and the pointer to its token."""
    url: str = ""
    token: str = field(default="", repr=False)   # inline: the escape hatch
    token_env: str = ""
    token_keyring: str = ""
    token_file: str = ""

    def resolve(self) -> str:
        if self.token_file:
            path = Path(os.path.expanduser(self.token_file))
            try:
                found = path.read_text(encoding="utf-8").strip()
                if found:
                    return found
                diag(f"[seren-psycheion] {path} is empty")
            except OSError as exc:
                diag(f"[seren-psycheion] can't read the token file {path} ({exc.strerror})")
        return resolve_token(inline=self.token or None, keyring_ref=self.token_keyring or None,
                             env_var=self.token_env or None)

    @classmethod
    def from_dict(cls, d: Any, default_url: str) -> "Endpoint":
        d = d if isinstance(d, dict) else {}
        return cls(url=str(d.get("url") or default_url), token=str(d.get("token") or ""),
                   token_env=str(d.get("token_env") or ""), token_keyring=str(d.get("token_keyring") or ""),
                   token_file=str(d.get("token_file") or ""))


@dataclass
class SomaConfig:
    """The body: the robot's control API and its voice server."""
    robot: Endpoint = field(default_factory=lambda: Endpoint(url=""))   # no robot until one's given
    voice: Endpoint = field(default_factory=lambda: Endpoint(url="http://127.0.0.1:7480"))

    @classmethod
    def from_dict(cls, d: Any) -> "SomaConfig":
        d = d if isinstance(d, dict) else {}
        return cls(robot=Endpoint.from_dict(d.get("robot"), ""),
                   voice=Endpoint.from_dict(d.get("voice"), "http://127.0.0.1:7480"))


@dataclass
class UpdatesConfig:
    """"Is there a newer seren-psycheion" checking. Cosmetic, opt-outable."""
    enabled: bool = True
    check_interval_hours: float = 6.0
    index_url: str = "https://pypi.org/pypi/{distribution}/json"
    allow_prerelease: bool = False

    @classmethod
    def from_dict(cls, d: Any) -> "UpdatesConfig":
        d = d if isinstance(d, dict) else {}
        out = cls()
        out.enabled = bool(d.get("enabled", out.enabled))
        try:
            out.check_interval_hours = float(d.get("check_interval_hours", out.check_interval_hours))
        except (TypeError, ValueError):
            pass
        out.index_url = str(d.get("index_url") or out.index_url)
        out.allow_prerelease = bool(d.get("allow_prerelease", out.allow_prerelease))
        return out


@dataclass
class PsycheionConfig:
    server: ServerConfig = field(default_factory=lambda: ServerConfig(host=DEFAULT_HOST, port=DEFAULT_PORT))
    soma: SomaConfig = field(default_factory=SomaConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)

    @property
    def host(self) -> str:
        return self.server.host

    @property
    def port(self) -> int:
        return self.server.port


def _candidate_paths(explicit: Optional[str]) -> List[Path]:
    out: List[Path] = []
    if explicit:
        out.append(Path(explicit))
    env = os.environ.get("SEREN_PSYCHEION_CONFIG")
    if env:
        out.append(Path(env))
    out.append(Path.home() / "seren-psycheion" / "seren-psycheion.yaml")
    return out


def _read_yaml(path: Optional[str]) -> Dict[str, Any]:
    for candidate in _candidate_paths(path):
        if not candidate.is_file():
            continue
        if not _HAS_YAML:
            diag(f"[seren-psycheion] pyyaml missing; ignoring {candidate}")
            return {}
        try:
            # encoding= on purpose: the locale codec on Windows would choke on the
            # sample's banner and the whole config would be silently ignored.
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError("top level of the config is not a mapping")
            diag(f"[seren-psycheion] config: {candidate}")
            return loaded
        except Exception as exc:  # noqa: BLE001 - lenient for a malformed file, and it says so
            diag(f"[seren-psycheion] {candidate} is malformed ({exc}); falling back to defaults")
            return {}
    return {}


def load_config(path: Optional[str] = None) -> PsycheionConfig:
    data = _read_yaml(path)
    raw = data.get("server")
    return PsycheionConfig(
        server=ServerConfig.from_dict(raw if isinstance(raw, dict) else {}, default_port=DEFAULT_PORT,
                                      default_host=DEFAULT_HOST),
        soma=SomaConfig.from_dict(data.get("soma")),
        updates=UpdatesConfig.from_dict(data.get("updates")),
    )
