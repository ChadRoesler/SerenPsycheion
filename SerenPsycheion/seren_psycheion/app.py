"""The FastAPI app: the viewer, and the body's API passed through with the body's own tokens.

The browser holds Psycheion's bearer (Meninges' token modal) and nothing else.
Psycheion holds the robot's control token and the voice server's bearer, makes
every call to the body itself through seren_sinew.soma, and never hands either
token back. A route here answers with what the body said, or 502 and where it
couldn't reach, never the token or the body's raw error text.

There is no camera route. A photo is taken only when the person in the room
says so (see seren_sinew.soma).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from seren_meninges.auth import bearer_auth_middleware
from seren_sinew.cluster.dtos import to_dict
from seren_sinew.request_log import RequestLoggingMiddleware
from seren_sinew.soma.client import SomaRobot, SomaVoice

from . import __version__ as APP_VERSION
from ._describe import ACCENT, NAME
from ._diag import diag
from .config import PsycheionConfig, load_config

try:
    from seren_meninges.viewer import render_from_dir
except Exception:  # noqa: BLE001 - the API stands without the shell, and the viewer says so
    render_from_dir = None  # type: ignore[assignment]

_VIEWER_DIR = Path(__file__).parent / "viewer" / "ui"


def _unreachable(what: str, url: str, exc: Exception) -> HTTPException:
    """502, saying where and roughly why, never what the body said in full (it can echo headers)."""
    if not url:
        return HTTPException(502, f"no {what} set up yet (its url in Psycheion's config is blank)")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        why = "refused the token" if code == 401 else f"answered {code}"
        return HTTPException(502, f"the {what} at {url} {why}")
    return HTTPException(502, f"can't reach the {what} at {url}")


def create_app(config: Optional[PsycheionConfig] = None, *,
               robot_transport: Optional[httpx.AsyncBaseTransport] = None,
               voice_transport: Optional[httpx.AsyncBaseTransport] = None) -> FastAPI:
    cfg = config or load_config()
    soma = cfg.soma

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Sources, not strings: asked again on a 401, so setting the robot up with a new token
        # (below) doesn't leave this Psycheion holding the old one.
        app.state.robot = SomaRobot(soma.robot.url, soma.robot.resolve, transport=robot_transport)
        app.state.voice = SomaVoice(soma.voice.url, soma.voice.resolve, transport=voice_transport)
        try:
            yield
        finally:
            await app.state.robot.aclose()
            await app.state.voice.aclose()

    app = FastAPI(title="SerenPsycheion", version=APP_VERSION, lifespan=lifespan)
    app.state.cfg = cfg

    # Open on loopback with no token, like the rest of the family; enforce_server
    # refuses a wider bind without one before this ever runs.
    app.add_middleware(bearer_auth_middleware(cfg.server.resolve_bearer()))
    app.add_middleware(RequestLoggingMiddleware, service_name=NAME, env_prefix="SEREN_PSYCHEION")

    try:
        from seren_meninges.updates import UpdateChecker
        app.state.updates = UpdateChecker(NAME, enabled=cfg.updates.enabled, index_url=cfg.updates.index_url,
                                          ttl_seconds=cfg.updates.check_interval_hours * 3600.0,
                                          allow_prerelease=cfg.updates.allow_prerelease, fallback_version=APP_VERSION)
    except Exception as exc:  # noqa: BLE001 - cosmetic, never fatal
        app.state.updates = None
        diag(f"[seren-psycheion] update checking unavailable ({exc})")

    @app.get("/")
    async def root(request: Request) -> dict:
        """The family's info route: who this is, where the page is, and the updates block, which every
        service carries with the same seven keys whatever state its checker is in (CI pins them)."""
        try:
            from seren_meninges.updates import updates_payload
            updates = await updates_payload(getattr(request.app.state, "updates", None),
                                            distribution=NAME, installed=APP_VERSION)
        except Exception as exc:  # noqa: BLE001 - an info route never 500s over a cosmetic check
            updates = {"status": "unavailable", "distribution": NAME, "installed": APP_VERSION, "latest": None,
                       "update_available": False, "checked_at": None,
                       "detail": f"update checking could not be constructed ({exc}): pip install -U seren-psycheion"}
        return {"service": NAME, "version": APP_VERSION, "viewer": "/viewer", "updates": updates}

    @app.get("/health")
    async def health():
        return {"ok": True, "service": NAME, "version": APP_VERSION}

    @app.get("/api/soma")
    async def soma_snapshot(request: Request) -> JSONResponse:
        """Everything the console's front page shows, in one call. Each side
        answers or says why it didn't; one being down doesn't hide the other."""
        out: dict[str, Any] = {"robot": None, "robot_error": None, "voice": None, "voice_error": None,
                               "voice_settings": None}
        try:
            out["robot"] = to_dict(await request.app.state.robot.status())
        except (httpx.HTTPError, ValueError) as exc:
            out["robot_error"] = _unreachable("robot", soma.robot.url, exc).detail
        try:
            out["voice"] = to_dict(await request.app.state.voice.status())
            out["voice_settings"] = to_dict(await request.app.state.voice.settings())
        except (httpx.HTTPError, ValueError) as exc:
            out["voice_error"] = _unreachable("voice server", soma.voice.url, exc).detail
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @app.post("/api/soma/robot/settings")
    async def robot_settings(request: Request):
        """{"dimmer": 0..100, "volume": 0..100, "brightness": 5..100}, any of them; saved on the robot."""
        body = await request.json()
        change: dict[str, int] = {}
        for key, low in (("dimmer", 0), ("volume", 0), ("brightness", 5)):
            if key in body:
                v = body[key]
                if not isinstance(v, int) or isinstance(v, bool) or not low <= v <= 100:
                    raise HTTPException(400, f"{key} is a whole number from {low} to 100")
                change[key] = v
        try:
            return to_dict(await request.app.state.robot.settings(**change))
        except (httpx.HTTPError, ValueError) as exc:
            raise _unreachable("robot", soma.robot.url, exc)

    @app.post("/api/soma/voice/settings")
    async def voice_settings(request: Request):
        """Any part of the voice server's settings; it checks them, and its reason for a no comes back as ours."""
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "expected an object of settings")
        try:
            return to_dict(await request.app.state.voice.set_settings(body))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                raise HTTPException(400, exc.response.text[:200])
            raise _unreachable("voice server", soma.voice.url, exc)
        except (httpx.HTTPError, ValueError) as exc:
            raise _unreachable("voice server", soma.voice.url, exc)

    def _setup_keys(body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise HTTPException(400, "expected an object: wifi, voice, tailnet")
        return {k: body[k] for k in ("wifi", "voice", "tailnet") if k in body}

    def _setup_failed(exc: Exception) -> HTTPException:
        """The voice server's own reason when it gave one (it never echoes a secret back);
        otherwise where it couldn't be reached."""
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (400, 409, 502):
            return HTTPException(exc.response.status_code, exc.response.text[:300])
        return _unreachable("voice server", soma.voice.url, exc)

    @app.post("/api/soma/setup")
    async def setup_robot(request: Request):
        """Set the robot up through the voice server: Wi-Fi, voice server (auto: the voice server
        itself), tailnet, and rotate_token for a new token. The robot saves them and reboots.
        A Wi-Fi password passes through here and isn't kept or logged."""
        body = await request.json()
        keys = _setup_keys(body)
        try:
            return await request.app.state.voice.setup_robot(
                keys, rotate_token=bool(body.get("rotate_token")), reboot=bool(body.get("reboot", True)))
        except (httpx.HTTPError, ValueError) as exc:
            raise _setup_failed(exc)

    @app.post("/api/soma/card")
    async def robot_card(request: Request):
        """soma.yaml for the robot's SD card, blanks filled by the voice server. Holds a token:
        handed straight to the browser as a download and never cached."""
        keys = _setup_keys(await request.json())
        try:
            text = await request.app.state.voice.robot_card(keys)
        except (httpx.HTTPError, ValueError) as exc:
            raise _setup_failed(exc)
        return Response(text, media_type="text/yaml",
                        headers={"Content-Disposition": 'attachment; filename="soma.yaml"', "Cache-Control": "no-store"})

    @app.get("/api/soma/firmware")
    async def firmware(request: Request):
        """What the robot runs, what the voice server has staged for it, and what's armed."""
        try:
            return await request.app.state.voice.firmware()
        except (httpx.HTTPError, ValueError) as exc:
            raise _setup_failed(exc)

    @app.post("/api/soma/firmware/fetch")
    async def firmware_fetch(request: Request):
        """{"version": "latest" | "0.1.1"}: the voice server fetches that release, checked against its sums."""
        body = await request.json()
        version = str((body or {}).get("version") or "latest") if isinstance(body, dict) else "latest"
        try:
            return await request.app.state.voice.fetch_firmware(version)
        except (httpx.HTTPError, ValueError) as exc:
            raise _setup_failed(exc)

    @app.put("/api/soma/firmware/upload")
    async def firmware_upload(request: Request, version: str):
        """An app image of your own (stack-chan.bin from a build), passed to the voice server to stage."""
        data = await request.body()
        try:
            return await request.app.state.voice.upload_firmware(version, data)
        except (httpx.HTTPError, ValueError) as exc:
            raise _setup_failed(exc)

    @app.post("/api/soma/firmware/install")
    async def firmware_install(request: Request):
        """{"version"}: arm it for the robot's next check-in and reboot the robot to take it."""
        body = await request.json()
        if not isinstance(body, dict) or not body.get("version"):
            raise HTTPException(400, "expected {\"version\": ...}")
        try:
            return await request.app.state.voice.install_firmware(str(body["version"]))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(404, exc.response.text[:300])
            raise _setup_failed(exc)
        except (httpx.HTTPError, ValueError) as exc:
            raise _setup_failed(exc)

    @app.get("/api/soma/touch")
    async def touch(request: Request, after: int = 0):
        try:
            return to_dict(await request.app.state.voice.touch(after=after))
        except (httpx.HTTPError, ValueError) as exc:
            raise _unreachable("voice server", soma.voice.url, exc)

    @app.get("/viewer", include_in_schema=False)
    async def viewer():
        if render_from_dir is None:
            return HTMLResponse("<h1>SerenPsycheion</h1><p>The SerenMeninges UI shell is not importable, "
                                "so the viewer can't render. <code>pip install seren-meninges</code></p>",
                                status_code=503)
        # no-store: the whole viewer is inlined, and a cached copy would show last week's page
        return HTMLResponse(headers={"Cache-Control": "no-store, must-revalidate"}, content=render_from_dir(
            _VIEWER_DIR, title="SerenPsycheion", brand="Seren<b>Psycheion</b>",
            subtitle=f"v{APP_VERSION} · the mind's workshop for a body", accent=ACCENT))

    return app
