"""Face Studio's server, in Psycheion: the faces folder and nothing outside it, the page (the viewer's
Faces tab) public but its data behind the bearer and its images behind a session cookie, saves that
don't trample, a face that travels as one .zip, and a send that builds, uploads and comes back installed."""
from __future__ import annotations

import io
import json
import struct
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
        self.frames = []

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
        if path in ("/api/emotion", "/api/twitch", "/api/leds"):
            if path == "/api/emotion":
                self.poses.append(json.loads(request.content))
            if path == "/api/leds":
                self.frames.append(json.loads(request.content)["frame"])
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


def test_a_pxp_can_be_any_size_and_must_be_a_pxp(tmp_path):
    """A .pxp is the whole drawing and has no cap (a 20.6 MB one was turned away at 16 MB, 14 Nov 2026).
    It lands whole or not at all, and something that isn't one doesn't replace the drawing that's there."""
    _a_face(tmp_path)
    big = struct.pack("<i", 0x31505850) + b"\x00" * (24 * 1024 * 1024)
    with _client(tmp_path) as c:
        ok = c.post("/api/studio/file", params={"dir": "mine", "name": "WrenFace.pxp"}, content=big, headers=AUTH)
        fake = c.post("/api/studio/file", params={"dir": "mine", "name": "WrenFace.pxp"}, content=b"not one", headers=AUTH)
        png = c.post("/api/studio/file", params={"dir": "mine", "name": "huge.png"}, content=b"\x89PNG" + b"\x00" * (17 * 1024 * 1024), headers=AUTH)
        home = c.get("/api/studio/home", headers=AUTH).json()
    d = tmp_path / "faces" / "mine"
    assert ok.status_code == 200 and (d / "WrenFace.pxp").stat().st_size == len(big)
    assert fake.status_code == 400 and (d / "WrenFace.pxp").stat().st_size == len(big)
    assert not list(d.glob(".*.uploading")) and "mine/WrenFace.pxp" in home["pxps"]
    assert png.status_code == 413 and "16 MB" in png.json()["detail"]


def test_a_deleted_face_goes_to_the_trash_and_off_the_list(tmp_path):
    """Deleting a face moves its folder into .trash, which the list never shows; nothing is erased. The
    faces folder itself, a folder that isn't a face, and one outside it are refused."""
    _a_face(tmp_path)
    _a_face(tmp_path, "keep")
    (tmp_path / "faces" / "plain").mkdir()
    with _client(tmp_path) as c:
        r = c.post("/api/studio/delete", params={"dir": "mine"}, headers=AUTH)
        home = c.get("/api/studio/home", headers=AUTH).json()
        again = c.post("/api/studio/delete", params={"dir": "mine"}, headers=AUTH)
        plain = c.post("/api/studio/delete", params={"dir": "plain"}, headers=AUTH)
        out = c.post("/api/studio/delete", params={"dir": "../faces"}, headers=AUTH)
        noauth = c.post("/api/studio/delete", params={"dir": "keep"})
    trashed = tmp_path / "faces" / r.json()["trashed"]
    assert r.status_code == 200 and (trashed / "face.yaml").is_file() and (trashed / "iris.png").is_file()
    assert not (tmp_path / "faces" / "mine").exists() and home["faces"] == ["keep"]
    assert again.status_code == 404 and plain.status_code == 400 and out.status_code in (400, 403)
    assert noauth.status_code == 401 and (tmp_path / "faces" / "keep" / "face.yaml").is_file()


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
        # up to a minute: the build converts 35 sprites, which is 10 s on a busy box, then the reboot wait
        for _ in range(600):
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


def test_the_rails_are_lit_led_by_led_and_kept_with_the_face(tmp_path):
    """The LEDs count round the head in a U (0 to 5 back along its right, 6 to 11 forward along its
    left). Each LED can have its own color, "#000000" for out; the front two of each rail (0, 1, 10,
    11) are dark unless colored, since the microphone and camera light them. One of the eight left
    out is the pose's color. The face keeps them through a save."""
    _a_face(tmp_path)
    robot = Robot()
    lights = {"color": "#ff0000", "brightness": 100, "leds": {"2": "#00ff00", "9": "#0000ff", "4": "#000000", "11": "#ffffff"}}
    with _client(tmp_path, robot) as c:
        lit = c.post("/api/studio/bot/leds", headers=AUTH, content=json.dumps({"lights": lights}))
        out = c.post("/api/studio/bot/leds", headers=AUTH, content=json.dumps({"lights": None}))
        bad = c.post("/api/studio/bot/leds", headers=AUTH, content=json.dumps({"lights": {"color": "pink"}}))
        doc = c.get("/api/studio/face", params={"dir": "mine"}, headers=AUTH).json()
        doc["doc"]["emotions"] = {**(doc["doc"].get("emotions") or {}), "happy": {"lights": lights}}
        saved = c.post("/api/studio/save", params={"dir": "mine", "since": doc["stamp"]}, headers=AUTH, content=json.dumps(doc["doc"]))
        back = c.get("/api/studio/face", params={"dir": "mine"}, headers=AUTH).json()["doc"]["emotions"]["happy"]["lights"]
    frame, dark = robot.frames
    assert lit.status_code == 200 and len(frame) == 12
    # the robot's own four are out unless colored (11 is); one of the eight set to black is out
    assert [frame[i] for i in (0, 1, 10)] == [[0, 0, 0]] * 3 and frame[11] == [168, 168, 168] and frame[4] == [0, 0, 0]
    assert frame[2] == [0, 168, 0] and frame[9] == [0, 0, 168] and frame[3] == frame[5] == frame[6] == frame[8] == [168, 0, 0]
    assert out.status_code == 200 and dark == [[0, 0, 0]] * 12
    assert bad.status_code == 400 and len(robot.frames) == 2
    assert saved.status_code == 200 and {str(k): v for k, v in back["leds"].items()} == lights["leds"]


def test_a_tried_pose_is_held_until_its_let_go(tmp_path):
    """A held try is locked on the robot (the firmware's "hold"): touches and the voice code's neutral
    show nothing until neutral is sent with hold false. The key always goes: while a pose is held,
    the robot refuses one that doesn't say."""
    robot = Robot()
    with _client(tmp_path, robot) as c:
        c.post("/api/studio/bot/try", headers=AUTH, content=json.dumps({"name": "smug", "hold": True}))
        c.post("/api/studio/bot/try", headers=AUTH, content=json.dumps({"name": "neutral"}))
    held, let_go = robot.poses
    assert held["name"] == "smug" and held["hold"] is True and "rest" not in held
    assert let_go["name"] == "neutral" and let_go["hold"] is False
