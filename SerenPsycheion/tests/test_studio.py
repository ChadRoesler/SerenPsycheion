"""Face Studio's server, in Psycheion: the faces folder and nothing outside it, the page (the viewer's
Faces tab) public but its data behind the bearer and its images behind a session cookie, saves that
don't trample, a face that travels as one .zip, and a send that builds, uploads and comes back installed."""
from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from seren_meninges import ServerConfig
from seren_psycheion import studio
from seren_psycheion.app import create_app
from seren_psycheion.config import Endpoint, PsycheionConfig, SomaConfig

from test_faces import _ogg, _starter

BEARER = "psy-secret"


class Robot:
    """A robot that takes a face and reboots: its uptime goes back once it has."""

    def __init__(self):
        self.up = 500000
        self.got = None
        self.calls = []
        self.poses = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/api/status":
            return httpx.Response(200, json={"ok": True, "firmware": "x", "uptimeMs": self.up,
                                             "facePush": "installed a new face: 89 files" if self.up < 1000 else None})
        if path == "/api/face":
            self.got = request.content
            return httpx.Response(200, json={"ok": True, "bytes": len(request.content)})
        if path == "/api/reboot":
            self.up = 500
            return httpx.Response(200, json={"ok": True})
        if path in ("/api/emotion", "/api/twitch"):
            if path == "/api/emotion":
                self.poses.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": "no"})


def _client(tmp_path, robot=None, bearer=BEARER):
    cfg = PsycheionConfig(server=ServerConfig(host="127.0.0.1", port=7429, bearer_token=bearer),
                          soma=SomaConfig(robot=Endpoint(url="http://bot:8080", token="rt"),
                                          voice=Endpoint(url="http://voice:7480", token="vt")),
                          faces_dir=str(tmp_path / "faces"))
    app = create_app(cfg, robot_transport=httpx.MockTransport(robot or Robot()), run_buildups=False)
    return TestClient(app)


AUTH = {"Authorization": f"Bearer {BEARER}"}


def _a_face(tmp_path, name="mine"):
    """A starter face in the faces folder."""
    src = _starter(tmp_path / "src")
    dest = tmp_path / "faces" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    return dest


# -- auth --------------------------------------------------------------------------------------------

def test_the_page_is_public_its_data_is_not_and_its_images_need_a_session(tmp_path):
    face = _a_face(tmp_path)
    with _client(tmp_path) as c:
        page = c.get("/viewer")
        home_no = c.get("/api/studio/home")
        img_no = c.get("/studio/file/mine/iris.png")
        home = c.get("/api/studio/home", headers=AUTH)
        sess = c.post("/api/studio/session", headers=AUTH)
        img = c.get("/studio/file/mine/iris.png")             # the cookie rides along now
    assert page.status_code == 200 and "<html" in page.text.lower()
    assert home_no.status_code == 401 and img_no.status_code == 401
    assert home.json()["faces"] == ["mine"] and sess.status_code == 200
    cookie = sess.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and BEARER not in cookie
    assert img.status_code == 200 and img.content == (face / "iris.png").read_bytes()


def test_the_studio_is_the_viewers_faces_tab_and_the_old_address_lands_on_it(tmp_path):
    """The tab's markup is in the viewer; its stylesheet and script are asked for by the page's <head>,
    which can't send a bearer either, so they're public too. /studio was the page until it moved in."""
    with _client(tmp_path) as c:
        viewer = c.get("/viewer").text
        old = c.get("/studio", params={"face": "mine", "sheet": "1"}, follow_redirects=False)
        css, js = c.get("/studio/studio.css"), c.get("/studio/studio.js")
        landed = c.get("/studio")
    assert 'id="faces"' in viewer and 'id="studioGrid"' in viewer and 'id="screen"' in viewer
    assert 'href="/studio/studio.css"' in viewer and 'src="/studio/studio.js"' in viewer
    assert old.status_code == 307 and old.headers["location"] == "/viewer?face=mine&sheet=1#faces"
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css") and "#faces" in css.text
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "TAB_SHOWN.faces" in js.text
    assert landed.status_code == 200 and 'id="faces"' in landed.text


def test_with_no_bearer_set_nothing_asks(tmp_path):
    _a_face(tmp_path)
    with _client(tmp_path, bearer="") as c:
        assert c.get("/studio/file/mine/iris.png").status_code == 200
        assert c.get("/api/studio/home").status_code == 200


# -- the faces folder, and nothing outside it -------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda c: c.get("/studio/file/../outside.txt"),
    lambda c: c.get("/studio/file/%2e%2e/outside.txt"),
    lambda c: c.get("/api/studio/face", params={"dir": "../.."}, headers=AUTH),
    lambda c: c.post("/api/studio/save", params={"dir": "../evil"}, content=b"{}", headers=AUTH),
    lambda c: c.post("/api/studio/file", params={"dir": "mine", "name": "../../x.png"}, content=b"x", headers=AUTH),
    lambda c: c.post("/api/studio/file", params={"dir": "mine", "name": "run.sh"}, content=b"x", headers=AUTH),
])
def test_nothing_escapes_the_faces_folder(tmp_path, call):
    _a_face(tmp_path)
    (tmp_path / "outside.txt").write_text("secret")
    with _client(tmp_path) as c:
        c.post("/api/studio/session", headers=AUTH)
        r = call(c)
    assert r.status_code in (400, 403, 404), r.status_code
    assert "secret" not in r.text and not (tmp_path / "x.png").exists()


# -- saving --------------------------------------------------------------------------------------------

def test_a_new_face_then_a_save_that_keeps_the_old_one_and_a_stale_save_refused(tmp_path):
    with _client(tmp_path) as c:
        new = c.post("/api/studio/save", params={"dir": "fresh", "new": "1"}, headers=AUTH,
                     content=json.dumps({"face": {"name": "fresh"}, "parts": {}, "emotions": {}}))
        again = c.post("/api/studio/save", params={"dir": "fresh", "new": "1"}, headers=AUTH, content=b"{}")
        stamp = c.get("/api/studio/face", params={"dir": "fresh"}, headers=AUTH).json()["stamp"]
        ok = c.post("/api/studio/save", params={"dir": "fresh", "since": stamp}, headers=AUTH,
                    content=json.dumps({"face": {"name": "fresh two"}, "parts": {}, "emotions": {}}))
        stale = c.post("/api/studio/save", params={"dir": "fresh", "since": stamp}, headers=AUTH, content=b"{}")
        forced = c.post("/api/studio/save", params={"dir": "fresh", "since": stamp, "force": "1"}, headers=AUTH,
                        content=json.dumps({"face": {"name": "forced"}}))
    d = tmp_path / "faces" / "fresh"
    assert new.status_code == 200 and again.status_code == 409
    assert ok.status_code == 200 and stale.status_code == 409 and stale.json()["changed"]
    assert forced.status_code == 200 and "forced" in (d / "face.yaml").read_text()
    assert len(list(d.glob("face.yaml.bak-*"))) >= 1


def test_uploads_art_a_pxp_and_a_sound_but_not_a_fake_sound(tmp_path):
    _a_face(tmp_path)
    good = io.BytesIO()
    _ogg(tmp_path / "s.ogg")
    with _client(tmp_path) as c:
        png = c.post("/api/studio/file", params={"dir": "mine", "name": "iris.png"}, content=b"\x89PNG new", headers=AUTH)
        snd = c.post("/api/studio/file", params={"dir": "mine", "name": "gasp.ogg"},
                     content=(tmp_path / "s.ogg").read_bytes(), headers=AUTH)
        fake = c.post("/api/studio/file", params={"dir": "mine", "name": "nope.ogg"}, content=b"RIFF....", headers=AUTH)
        sounds = c.get("/api/studio/face", params={"dir": "mine"}, headers=AUTH).json()["sounds"]
    d = tmp_path / "faces" / "mine"
    assert png.status_code == 200 and (d / ".studio-backup" / "iris.png").is_file()   # the one it replaced
    assert snd.json()["name"] == "sounds/gasp.ogg" and sounds == ["sounds/gasp.ogg"]
    assert fake.status_code == 400 and "ffmpeg" in fake.json()["detail"]


# -- a face travels as one file ------------------------------------------------------------------------

def test_export_then_import_is_the_same_face(tmp_path):
    face = _a_face(tmp_path)
    (face / "face.yaml.bak-20261109-000000").write_text("old")
    (face / "build").mkdir()
    (face / "build" / "f_base.bin").write_bytes(b"built")
    with _client(tmp_path) as c:
        z = c.get("/api/studio/export", params={"dir": "mine"}, headers=AUTH)
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        imp = c.post("/api/studio/import", params={"dir": "theirs"}, content=z.content, headers=AUTH)
        twice = c.post("/api/studio/import", params={"dir": "theirs"}, content=z.content, headers=AUTH)
    assert "face.yaml" in names and "iris.png" in names
    assert not any(n.startswith("build/") or ".bak-" in n for n in names)          # no builds, no backups
    assert imp.status_code == 200 and twice.status_code == 409
    theirs = tmp_path / "faces" / "theirs"
    assert (theirs / "face.yaml").read_text() == (face / "face.yaml").read_text()


def test_an_import_takes_a_wrapped_zip_and_drops_anything_that_climbs_out(tmp_path):
    face = _a_face(tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("myface/face.yaml", (face / "face.yaml").read_text())
        z.writestr("myface/iris.png", b"png")
        z.writestr("myface/../../escaped.txt", b"bad")
        z.writestr("myface/.hidden", b"bad")
    with _client(tmp_path) as c:
        r = c.post("/api/studio/import", params={"dir": "wrapped"}, content=buf.getvalue(), headers=AUTH)
        bad = c.post("/api/studio/import", params={"dir": "nope"}, content=b"not a zip", headers=AUTH)
    d = tmp_path / "faces" / "wrapped"
    assert r.status_code == 200 and (d / "face.yaml").is_file() and (d / "iris.png").is_file()
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "faces" / "escaped.txt").exists()
    assert not (d / ".hidden").exists() and bad.status_code == 400


# -- the robot -------------------------------------------------------------------------------------------

def test_send_builds_uploads_reboots_and_comes_back_installed(tmp_path):
    _a_face(tmp_path)
    robot = Robot()
    with _client(tmp_path, robot) as c:
        assert c.get("/api/studio/bot", headers=AUTH).json()["fromConfig"] is True
        assert c.post("/api/studio/bot/send", params={"dir": "mine"}, headers=AUTH).status_code == 200
        busy = c.post("/api/studio/bot/send", params={"dir": "mine"}, headers=AUTH)
        import time
        for _ in range(100):
            job = c.get("/api/studio/bot", headers=AUTH).json()["job"]
            if not job["running"]:
                break
            time.sleep(0.1)
    from seren_sinew.soma.bundle import unpack
    assert busy.status_code == 409
    assert job["ok"] is True and job["phase"].startswith("Installed"), job
    assert "face.json" in unpack(robot.got) and robot.calls.count("/api/reboot") == 1


def test_try_sends_the_pose_being_worked_on(tmp_path):
    robot = Robot()
    with _client(tmp_path, robot) as c:
        r = c.post("/api/studio/bot/try", headers=AUTH,
                   content=json.dumps({"name": "happy", "lights": {"color": "#ff4fa0", "brightness": 50}}))
    assert r.status_code == 200 and robot.calls == ["/api/emotion", "/api/twitch"]


def test_a_tried_pose_is_held_until_its_let_go(tmp_path):
    """The robot goes back to neutral on every connect, listen and hang up. A held try stands in for
    neutral there (the firmware's rest), so it stays; trying neutral without hold lets it go."""
    robot = Robot()
    with _client(tmp_path, robot) as c:
        c.post("/api/studio/bot/try", headers=AUTH, content=json.dumps({"name": "smug", "hold": True}))
        c.post("/api/studio/bot/try", headers=AUTH, content=json.dumps({"name": "neutral"}))
    held, let_go = robot.poses
    assert held["name"] == "smug" and held["rest"] is True
    assert let_go["name"] == "neutral" and "rest" not in let_go
