"""Face Studio, served by Psycheion: the page at /studio and the routes it draws on.

Came from SerenSoma's firmware/tools/face_studio.py on 9 Nov 2026. That was a server of its own on
127.0.0.1, opening a folder on the machine it ran on. Here the faces live in Psycheion's faces folder
(faces_dir, default ~/seren-psycheion/faces), which for a Psycheion on another box is on that box: art
comes in by upload, and a face goes out (to share it) as one .zip. The robot is the one Psycheion's
config names, so the page no longer keeps a robot address or token of its own.

Auth. The page and its images (GET /studio/...) are public to the bearer middleware, because an
<img src> can't send a header; the images check a session cookie instead, which POST
/api/studio/session hands out to a page that does have the bearer (HttpOnly, SameSite=Strict, a random
value, never the bearer itself, never in a URL). Everything that reads or writes a face's data is under
/api/studio/ and behind the bearer as usual. With no bearer configured (loopback), there's nothing to
check, as for the rest of Psycheion.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import secrets
import shutil
import struct
import tempfile
import time
import zipfile
import zlib
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Optional
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a dependency
    yaml = None

from .faces import face as facetool

PAGE_DIR = Path(__file__).parent / "studio_ui"
SKIP_DIRS = {"build", "__pycache__", "node_modules", "preview"}
SESSION_COOKIE = "psy_studio"
SESSION_S = 12 * 3600
MAX_UPLOAD = 16 * 1024 * 1024        # one file; a .pxp of a busy face is a few MB
MAX_IMPORT = 64 * 1024 * 1024        # one shared face, zipped
FILE_NAME = re.compile(r"[A-Za-z0-9_.\-]+")
UPLOAD_KINDS = {".png": "image/png", ".pxp": "application/octet-stream", ".ogg": "audio/ogg"}
FACE_DIR = re.compile(r"[A-Za-z0-9_\-][A-Za-z0-9_.\- ]{0,63}(/[A-Za-z0-9_\-][A-Za-z0-9_.\- ]{0,63}){0,3}")


# ------------------------------------------------------------------ the faces folder

class Store:
    """The faces folder. Every path the page sends is resolved inside it, or refused."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def inside(self, rel: str) -> Path:
        p = (self.root / unquote(rel or "")).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError(rel)
        return p

    def face_dir(self, rel: str) -> Path:
        """A face's folder: one to four plain path parts, no dot-dot, nothing hidden."""
        if not FACE_DIR.fullmatch(rel or ""):
            raise ValueError("a face's name is letters, digits, spaces, _ . - (and / for a folder of faces)")
        return self.inside(rel)

    def walk(self, base: Optional[Path] = None, depth: int = 4):
        base = base or self.root
        if depth < 0:
            return
        try:
            entries = sorted(base.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_dir():
                if e.name not in SKIP_DIRS and not e.name.startswith("."):
                    yield from self.walk(e, depth - 1)
            else:
                yield e


# ------------------------------------------------------------------ face.yaml (as Face Studio wrote it)

def emit_yaml(doc, header):
    """face.yaml in the hand-written shape: blocks for sections, one flow line
    per decorator and emotion, flow lists for positions and frames. Checked by
    reading it back; on any mismatch the plain PyYAML dump is written instead."""
    plain = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-/ ]*$")
    special = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}

    def scalar(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if v is None:
            return "null"
        if isinstance(v, (int, float)):
            return repr(v)
        s = str(v)
        if plain.match(s) and s.lower() not in special and not s.endswith(" "):
            return s
        return json.dumps(s)

    def flow(v):
        if isinstance(v, dict):
            return "{" + ", ".join(f"{k}: {flow(x)}" for k, x in v.items()) + "}"
        if isinstance(v, list):
            return "[" + ", ".join(flow(x) for x in v) + "]"
        return scalar(v)

    lines = []

    def block(d, indent, flow_children=False):
        pad = "  " * indent
        for k, v in d.items():
            if isinstance(v, dict) and v and not flow_children:
                lines.append(f"{pad}{k}:")
                block(v, indent + 1, flow_children=k in ("decorators", "emotions"))
            else:
                lines.append(f"{pad}{k}: {flow(v)}")

    for top in ("face", "parts", "emotions"):
        if top in doc:
            if lines:
                lines.append("")
            if isinstance(doc[top], dict) and doc[top]:
                lines.append(f"{top}:")
                block(doc[top], 1, flow_children=top == "emotions")
            else:
                lines.append(f"{top}: {flow(doc[top] or {})}")
    for k, v in doc.items():
        if k not in ("face", "parts", "emotions"):
            lines.append(yaml.safe_dump({k: v}, sort_keys=False).rstrip())
    text = (header + "\n" if header else "") + "\n".join(lines) + "\n"
    if yaml.safe_load(text) != doc:
        text = (header + "\n" if header else "") + yaml.safe_dump(doc, sort_keys=False)
    return text


def header_of(text):
    out = []
    for line in text.splitlines():
        if line.startswith("#") or (out and not line.strip()):
            out.append(line)
        else:
            break
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


# ------------------------------------------------------------------ PixlPunkt .pxp (as Face Studio read it)

def pxp_layers(path):
    """Every raster layer in a .pxp: name, visible, pixels (BGRA, straight alpha).

    Finds layers by their pixel block (width, height, byte count = w*h*4) and
    reads the fields PixlPunkt writes just before it: name, visible, locked,
    blend, opacity. Folder nesting is not needed for picking parts."""
    b = path.read_bytes()
    if struct.unpack_from("<i", b, 0)[0] != 0x31505850:
        raise ValueError("not a PixlPunkt document")
    p, n, s = 8, 0, 0
    while True:
        c = b[p]
        p += 1
        n |= (c & 0x7F) << s
        s += 7
        if not c & 0x80:
            break
    p += n
    w, h = struct.unpack_from("<ii", b, p)
    pat = struct.pack("<iii", w, h, w * h * 4)
    out, off = [], b.find(pat)
    while off != -1:
        end, name = off - 7, f"layer {len(out)}"
        for length in range(0, 128):
            s0 = end - length - 1
            if s0 >= 0 and b[s0] == length:
                try:
                    cand = b[s0 + 1:end].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if cand.isprintable():
                    name = cand
                    break
        out.append({"name": name, "visible": bool(b[off - 7]), "w": w, "h": h,
                    "pixels": b[off + 12: off + 12 + w * h * 4]})
        off = b.find(pat, off + 12 + w * h * 4)
    return out


def bbox(px, w, h):
    a = px[3::4]
    rows = [any(a[y * w:(y + 1) * w]) for y in range(h)]
    if not any(rows):
        return None
    y0, y1 = rows.index(True), h - rows[::-1].index(True)
    cols = [any(a[y * w + x] for y in range(y0, y1)) for x in range(w)]
    x0, x1 = cols.index(True), w - cols[::-1].index(True)
    return [x0, y0, x1, y1]


def png_rgba(w, h, bgra):
    rgba = bytearray(bgra)
    rgba[0::4], rgba[2::4] = bgra[2::4], bgra[0::4]
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


_pxp_cache: dict = {}


def cached_layers(path):
    key = (str(path), path.stat().st_mtime_ns)
    if key not in _pxp_cache:
        _pxp_cache.clear()
        layers = pxp_layers(path)
        for L in layers:
            L["bbox"] = bbox(L["pixels"], L["w"], L["h"])
        _pxp_cache[key] = layers
    return _pxp_cache[key]


# ------------------------------------------------------------------ building, and the robot

def build_bundle(face_dir: Path) -> bytes:
    """Build the face (seren_psycheion.faces.face) and pack it the way the robot unpacks it."""
    from seren_sinew.soma.bundle import MAX_BUNDLE, pack
    with tempfile.TemporaryDirectory() as tmp:
        facetool.cmd_build(NS(source=str(face_dir), out=tmp, prefix="f", dry_run=False))
        files = {p.name: p.read_bytes() for p in Path(tmp).iterdir() if p.is_file()}
    data = pack(files)
    if len(data) > MAX_BUNDLE:
        raise facetool.FaceError(f"the face is {len(data) // 1024} KB, and the robot holds {MAX_BUNDLE // 1024} KB")
    return data


def robot_says(e: Exception, url: str) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 401:
            return "the robot refused the token"
        try:
            return e.response.json().get("error") or f"the robot said {e.response.status_code}"
        except ValueError:
            return f"the robot said {e.response.status_code}"
    return f"can't reach the robot at {url or '(none set up)'}"


class Job:
    """One robot job at a time (a send or a restore), and what it's doing, for the page to poll."""

    def __init__(self):
        self.running, self.phase, self.ok = False, "", None

    def as_dict(self):
        return {"running": self.running, "phase": self.phase, "ok": self.ok}


async def wait_for_reboot(robot, was_up: int, seconds: float = 90, step: float = 1.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        await asyncio.sleep(step)
        try:
            s = await robot.status()
        except (httpx.HTTPError, ValueError):
            continue
        if s.uptimeMs < was_up:
            return s
    return None


# ------------------------------------------------------------------ the routes

def studio_router(cfg) -> APIRouter:
    r = APIRouter()
    store = Store(cfg.faces_dir)
    sessions: dict[str, float] = {}
    job = Job()
    bearer = cfg.server.resolve_bearer()

    def page_session_ok(request: Request) -> bool:
        if not bearer:
            return True
        sid = request.cookies.get(SESSION_COOKIE, "")
        until = sessions.get(sid)
        return bool(until and until > time.time())

    def need_session(request: Request):
        if not page_session_ok(request):
            raise HTTPException(401, "open Face Studio from its page, with Psycheion's token")

    def failed(e: Exception):
        if isinstance(e, PermissionError):
            return HTTPException(403, "outside the faces folder")
        if isinstance(e, facetool.FaceError):
            return HTTPException(400, str(e))
        return HTTPException(400, f"{type(e).__name__}: {e}")

    # -- the page -----------------------------------------------------------------------------------

    @r.get("/studio", include_in_schema=False)
    async def page():
        return HTMLResponse((PAGE_DIR / "face_studio.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})

    @r.get("/studio/stackChanTop.png", include_in_schema=False)
    async def head_top():
        return Response((PAGE_DIR / "stackChanTop.png").read_bytes(), media_type="image/png")

    @r.post("/api/studio/session")
    async def session():
        """Swap the bearer (already checked by the middleware) for the cookie the page's images need."""
        for sid in [k for k, until in sessions.items() if until < time.time()]:
            sessions.pop(sid, None)
        sid = secrets.token_urlsafe(32)
        sessions[sid] = time.time() + SESSION_S
        resp = JSONResponse({"ok": True})
        resp.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_S, httponly=True, samesite="strict", path="/studio")
        return resp

    @r.get("/studio/file/{rel:path}", include_in_schema=False)
    async def file(request: Request, rel: str):
        need_session(request)
        try:
            p = store.inside(rel)
            kind = UPLOAD_KINDS.get(p.suffix.lower(), "application/octet-stream")
            return Response(p.read_bytes(), media_type=kind, headers={"Cache-Control": "no-store"})
        except (PermissionError, FileNotFoundError, IsADirectoryError) as e:
            raise HTTPException(404 if not isinstance(e, PermissionError) else 403, "no such file")

    @r.get("/studio/pxp-layer", include_in_schema=False)
    async def pxp_layer(request: Request, path: str, i: int):
        need_session(request)
        try:
            L = cached_layers(store.inside(path))[i]
        except (PermissionError, FileNotFoundError, ValueError, IndexError) as e:
            raise failed(e)
        return Response(png_rgba(L["w"], L["h"], L["pixels"]), media_type="image/png")

    # -- the faces ----------------------------------------------------------------------------------

    @r.get("/api/studio/home")
    async def home():
        store.ensure()
        faces, pxps = [], []
        for f in store.walk():
            if f.name in ("face.yaml", "face.yml"):
                faces.append(f.parent.relative_to(store.root).as_posix())
            elif f.suffix.lower() == ".pxp":
                pxps.append(f.relative_to(store.root).as_posix())
        waiting = sorted({str(Path(p).parent.as_posix()) for p in pxps} - set(faces))
        return {"root": "Psycheion's faces", "faces": faces, "waiting": waiting, "pxps": pxps,
                "defaults": facetool.DEFAULT_EMOTIONS, "screen": [facetool.SCREEN_W, facetool.SCREEN_H],
                "max_asset_name": facetool.MAX_ASSET_NAME}

    @r.get("/api/studio/face")
    async def face(dir: str):
        try:
            d = store.inside(dir)
        except PermissionError as e:
            raise failed(e)
        y = next((d / n for n in ("face.yaml", "face.yml") if (d / n).exists()), None)
        doc = (yaml.safe_load(y.read_text(encoding="utf-8")) or {}) if y else {}
        files = sorted(p.name for p in d.iterdir() if p.suffix.lower() == ".png") if d.is_dir() else []
        sounds = sorted(p.relative_to(d).as_posix() for p in d.rglob("*.ogg")) if d.is_dir() else []
        return {"doc": doc, "files": files, "sounds": sounds, "stamp": str(y.stat().st_mtime_ns) if y else ""}

    @r.get("/api/studio/pxp")
    async def pxp(path: str):
        try:
            layers = cached_layers(store.inside(path))
        except (PermissionError, FileNotFoundError, ValueError) as e:
            raise failed(e)
        return [{"i": i, "name": L["name"], "visible": L["visible"], "bbox": L["bbox"]} for i, L in enumerate(layers)]

    @r.post("/api/studio/save")
    async def save(request: Request, dir: str, new: str = "", since: str = "", force: str = ""):
        try:
            d = store.face_dir(dir)
        except (PermissionError, ValueError) as e:
            raise failed(e)
        y = d / "face.yaml"
        if new and y.exists():
            raise HTTPException(409, f"{dir} already has a face. Pick another name.")
        # something else wrote it after this page opened it, and this page doesn't have that
        if since and not force and y.exists() and str(y.stat().st_mtime_ns) != since:
            return JSONResponse({"changed": True, "detail": "face.yaml changed on disk since this page opened it",
                                 "error": "face.yaml changed on disk since this page opened it"}, status_code=409)
        try:
            doc = json.loads(await request.body())
        except ValueError:
            raise HTTPException(400, "expected the face as JSON")
        d.mkdir(parents=True, exist_ok=True)
        header = "# A Seren face -- see SerenSoma's firmware/docs/custom-faces.md"
        if y.exists():
            header = header_of(y.read_text(encoding="utf-8")) or header
            shutil.copy2(y, d / f"face.yaml.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        y.write_text(emit_yaml(doc, header), encoding="utf-8")
        return {"ok": True, "stamp": str(y.stat().st_mtime_ns)}

    @r.post("/api/studio/file")
    async def upload(request: Request, dir: str, name: str):
        """One file into a face's folder: a PNG, a .pxp, or a sound (.ogg). The version it replaces is
        kept in .studio-backup."""
        try:
            d = store.face_dir(dir)
        except (PermissionError, ValueError) as e:
            raise failed(e)
        ext = Path(name).suffix.lower()
        if Path(name).name != name or not FILE_NAME.fullmatch(name) or ext not in UPLOAD_KINDS:
            raise HTTPException(400, "file names are letters, digits, _ . - and end in .png, .pxp or .ogg")
        body = await request.body()
        if len(body) > MAX_UPLOAD:
            raise HTTPException(413, f"that's over {MAX_UPLOAD // (1024 * 1024)} MB")
        if ext == ".ogg" and (body[:4] != b"OggS" or b"OpusHead" not in body[:128]):
            raise HTTPException(400, "that isn't Ogg Opus. Make one with: ffmpeg -i in.wav -ac 1 -c:a libopus -b:a 24k out.ogg")
        target = (d / "sounds" / name) if ext == ".ogg" else (d / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            keep = d / ".studio-backup"
            keep.mkdir(exist_ok=True)
            shutil.copy2(target, keep / name)
        target.write_bytes(body)
        return {"ok": True, "name": target.relative_to(d).as_posix()}

    @r.get("/api/studio/export")
    async def export(dir: str):
        """The face as one file to share: its face.yaml, art and sounds, without builds or backups."""
        try:
            d = store.inside(dir)
        except PermissionError as e:
            raise failed(e)
        if not (d / "face.yaml").is_file():
            raise HTTPException(404, "no face there")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(d.rglob("*")):
                rel = p.relative_to(d)
                if p.is_file() and not any(part.startswith(".") or part in SKIP_DIRS for part in rel.parts) \
                        and ".bak-" not in p.name:
                    z.write(p, rel.as_posix())
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", d.name) or "face"
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}.zip"', "Cache-Control": "no-store"})

    @r.post("/api/studio/import")
    async def import_face(request: Request, dir: str):
        """A shared face (.zip, as export makes it) into a new folder. Never over a face that's there."""
        try:
            d = store.face_dir(dir)
        except (PermissionError, ValueError) as e:
            raise failed(e)
        if d.exists() and any(d.iterdir()):
            raise HTTPException(409, f"{dir} is already a face. Pick another name.")
        body = await request.body()
        if len(body) > MAX_IMPORT:
            raise HTTPException(413, f"that's over {MAX_IMPORT // (1024 * 1024)} MB")
        try:
            z = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile:
            raise HTTPException(400, "that isn't a .zip")
        names = [n for n in z.namelist() if not n.endswith("/")]
        # a zip made by hand often wraps everything in one folder: take what's inside it
        prefix = ""
        if names and "face.yaml" not in names:
            tops = {n.split("/", 1)[0] for n in names}
            if len(tops) == 1 and f"{next(iter(tops))}/face.yaml" in names:
                prefix = next(iter(tops)) + "/"
        if f"{prefix}face.yaml" not in names:
            raise HTTPException(400, "there's no face.yaml in it")
        written = 0
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            rel = n[len(prefix):]
            parts = rel.split("/")
            if not rel or any(not FILE_NAME.fullmatch(p) or p.startswith(".") for p in parts) or len(parts) > 3:
                continue                          # nothing odd, nothing hidden, nothing that climbs out
            target = store.inside(f"{dir}/{rel}")
            info = z.getinfo(n)
            written += info.file_size
            if written > MAX_IMPORT * 2:
                raise HTTPException(413, "it unpacks too big")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
        return {"ok": True, "dir": dir}

    # -- the robot ------------------------------------------------------------------------------------

    def robot(request: Request):
        return request.app.state.robot

    @r.get("/api/studio/bot")
    async def bot():
        url = cfg.soma.robot.url
        return {"url": url, "hasToken": bool(cfg.soma.robot.resolve()), "fromConfig": True, "job": job.as_dict()}

    @r.post("/api/studio/bot/try")
    async def bot_try(request: Request):
        b = json.loads(await request.body() or b"{}")
        try:
            lit = facetool.lights_rgb(b.get("lights")) or [0, 0, 0]
            await robot(request).emotion(b["name"], lights=lit, lightsPulse=facetool.lights_pulse(b.get("lights")),
                                         look=facetool.sided_pair(b.get("look")), brow=facetool.sided_pair(b.get("brow")),
                                         mouthCycle=int(b.get("mouthCycle") or 0), roll=int(b.get("roll") or 0))
            await robot(request).twitch(b.get("twitch"))
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise HTTPException(502, robot_says(e, cfg.soma.robot.url))
        return {"ok": True}

    async def run_send(request: Request, face_dir: Path):
        rb = robot(request)
        try:
            job.phase = "Building the face…"
            data = await asyncio.to_thread(build_bundle, face_dir)
            was_up = (await rb.status()).uptimeMs
            job.phase = f"Sending {len(data) // 1024} KB to the robot…"
            await rb.send_face(data)
            job.phase = "Rebooting the robot…"
            await rb.reboot()
            s = await wait_for_reboot(rb, was_up)
            if s is None:
                raise RuntimeError("the robot took the face but didn't come back. Check that it's on.")
            result = s.facePush or "the robot came back without saying what happened"
            job.phase, job.ok = result[0].upper() + result[1:] + ".", result.startswith("installed")
        except facetool.FaceError as e:
            job.phase, job.ok = f"the build failed: {e}", False
        except (httpx.HTTPError, ValueError) as e:
            job.phase, job.ok = robot_says(e, cfg.soma.robot.url) + ".", False
        except RuntimeError as e:
            job.phase, job.ok = str(e), False
        finally:
            job.running = False

    async def run_restore(request: Request):
        rb = robot(request)
        try:
            was_up = (await rb.status()).uptimeMs
            job.phase = "Asking the robot for its previous face…"
            await rb.restore_face()
            await rb.reboot()
            s = await wait_for_reboot(rb, was_up)
            if s is None:
                raise RuntimeError("the robot didn't come back. Check that it's on.")
            result = s.facePush or "the robot came back without saying what happened"
            job.phase, job.ok = result[0].upper() + result[1:] + ".", result.startswith("previous")
        except (httpx.HTTPError, ValueError) as e:
            job.phase, job.ok = robot_says(e, cfg.soma.robot.url) + ".", False
        except RuntimeError as e:
            job.phase, job.ok = str(e), False
        finally:
            job.running = False

    def start(coro):
        if job.running:
            coro.close()
            raise HTTPException(409, "the robot is busy with the last request")
        job.running, job.phase, job.ok = True, "Starting…", None
        asyncio.ensure_future(coro)

    @r.post("/api/studio/bot/send")
    async def bot_send(request: Request, dir: str):
        try:
            d = store.inside(dir)
        except PermissionError as e:
            raise failed(e)
        start(run_send(request, d))
        return {"ok": True}

    @r.post("/api/studio/bot/restore")
    async def bot_restore(request: Request):
        start(run_restore(request))
        return {"ok": True}

    return r
