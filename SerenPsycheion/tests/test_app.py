"""Psycheion's app against a fake robot and a fake voice server: what passes through, what doesn't, and that
the body's tokens never come back out."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from seren_meninges import ServerConfig
from seren_psycheion.app import create_app
from seren_psycheion.config import Endpoint, PsycheionConfig, SomaConfig, load_config

ROBOT_TOKEN, VOICE_TOKEN = "robot-secret-123", "voice-secret-456"
STATUS = {"ok": True, "firmware": "1.6.2", "uptimeMs": 60000, "resetReason": "software",
          "leds": "000000" * 12, "settings": {"dimmer": 80, "volume": 70, "brightness": 75, "night": None,
                                              "applied": {"dimmer": 80, "volume": 70, "brightness": 75}},
          "batteryLevel": 90, "charging": False, "freeInternalHeap": 44000, "freePsram": 6000000, "face": "studio"}
VOICE_SETTINGS = {"night": {"enabled": False, "start": "22:30", "end": "07:00", "dimmer": 15, "volume": 30, "brightness": 20},
                  "work_hours": {"days": [0, 1, 2, 3, 4], "start": 8, "end": 17},
                  "overwhelm": {"enabled": True, "seconds": 15, "gap_seconds": 3, "rest_seconds": 120}, "night_now": False}


class Body:
    """A fake robot and voice server that check the tokens they're handed."""

    def __init__(self, robot_up=True):
        self.robot_up = robot_up
        self.seen = []

    def robot(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(("robot", request.method, request.url.path, request.content))
        if not self.robot_up:
            raise httpx.ConnectError("down", request=request)
        if request.headers.get("X-StackChan-Token") != ROBOT_TOKEN:
            return httpx.Response(401, json={"error": "bad or missing token " + request.headers.get("X-StackChan-Token", "")})
        if request.url.path == "/api/status":
            return httpx.Response(200, json=STATUS)
        if request.url.path == "/api/settings":
            body = json.loads(request.content or b"{}")
            s = dict(STATUS["settings"], **body)
            return httpx.Response(200, json={"ok": True, "settings": s})
        return httpx.Response(404, json={"error": "no"})

    def voice(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(("voice", request.method, request.url.path, request.content))
        if request.headers.get("Authorization") != f"Bearer {VOICE_TOKEN}":
            return httpx.Response(401, text="bad token")
        if request.url.path == "/status":
            return httpx.Response(200, json={"ok": True, "device": False, "speaking": False})
        if request.url.path == "/settings" and request.method == "GET":
            return httpx.Response(200, json=VOICE_SETTINGS)
        if request.url.path == "/settings":
            body = json.loads(request.content)
            if body.get("night", {}).get("start") == "99:00":
                return httpx.Response(400, text="night.start is a time like 22:30")
            return httpx.Response(200, json=VOICE_SETTINGS)
        if request.url.path == "/touch":
            return httpx.Response(200, json={"events": [{"seq": 1, "t": 1.0, "kind": "scritch", "t_ms": 5, "ms": 100,
                                                         "showed": "melty", "overwhelmed": False}],
                                             "last": 1, "overwhelm": {"running": True, "progress": 0.5}})
        return httpx.Response(404)


def client(body: Body, bearer: str = "") -> TestClient:
    cfg = PsycheionConfig(server=ServerConfig(host="127.0.0.1", port=7429, bearer_token=bearer),
                          soma=SomaConfig(robot=Endpoint(url="http://bot:8080", token=ROBOT_TOKEN),
                                          voice=Endpoint(url="http://voice:7480", token=VOICE_TOKEN)))
    app = create_app(cfg, robot_transport=httpx.MockTransport(body.robot), voice_transport=httpx.MockTransport(body.voice))
    return TestClient(app)


def test_snapshot_carries_both_sides():
    with client(Body()) as c:
        r = c.get("/api/soma").json()
    assert r["robot"]["face"] == "studio" and r["voice"]["ok"] and r["voice_settings"]["night"]["start"] == "22:30"
    assert r["robot_error"] is None and r["voice_error"] is None


def test_one_side_down_doesnt_hide_the_other():
    with client(Body(robot_up=False)) as c:
        r = c.get("/api/soma").json()
    assert r["robot"] is None and "can't reach the robot at http://bot:8080" in r["robot_error"]
    assert r["voice"]["ok"]


def test_the_bodys_tokens_never_come_back_out():
    with client(Body()) as c:
        pages = [c.get("/api/soma").text, c.get("/viewer").text, c.get("/api/soma/touch").text,
                 c.post("/api/soma/robot/settings", json={"dimmer": 40}).text]
    for page in pages:
        assert ROBOT_TOKEN not in page and VOICE_TOKEN not in page


def test_a_refused_token_says_so_without_echoing_anything():
    body = Body()
    cfg_bad = PsycheionConfig(soma=SomaConfig(robot=Endpoint(url="http://bot:8080", token="wrong-one"),
                                              voice=Endpoint(url="http://voice:7480", token=VOICE_TOKEN)))
    app = create_app(cfg_bad, robot_transport=httpx.MockTransport(body.robot), voice_transport=httpx.MockTransport(body.voice))
    with TestClient(app) as c:
        r = c.get("/api/soma").json()
    assert r["robot_error"] == "the robot at http://bot:8080 refused the token"
    assert "wrong-one" not in json.dumps(r)


def test_robot_settings_are_checked_then_passed_on():
    body = Body()
    with client(body) as c:
        ok = c.post("/api/soma/robot/settings", json={"dimmer": 40, "brightness": 50})
        too_dim = c.post("/api/soma/robot/settings", json={"brightness": 2})
        not_a_number = c.post("/api/soma/robot/settings", json={"volume": "loud"})
    assert ok.status_code == 200 and ok.json()["dimmer"] == 40
    assert too_dim.status_code == 400 and "5 to 100" in too_dim.json()["detail"]
    assert not_a_number.status_code == 400
    sent = [json.loads(b) for who, m, p, b in body.seen if p == "/api/settings"]
    assert sent == [{"dimmer": 40, "brightness": 50}]


def test_voice_settings_pass_through_and_its_no_is_ours():
    with client(Body()) as c:
        ok = c.post("/api/soma/voice/settings", json={"night": {"enabled": True}})
        no = c.post("/api/soma/voice/settings", json={"night": {"start": "99:00"}})
    assert ok.status_code == 200 and ok.json()["overwhelm"]["seconds"] == 15
    assert no.status_code == 400 and "time like 22:30" in no.json()["detail"]


def test_touch_feed():
    with client(Body()) as c:
        r = c.get("/api/soma/touch", params={"after": 0}).json()
    assert r["events"][0]["showed"] == "melty" and r["overwhelm"]["progress"] == 0.5


def test_bearer_guards_the_api_but_not_the_page():
    with client(Body(), bearer="psy-token") as c:
        assert c.get("/viewer").status_code == 200
        assert c.get("/health").status_code == 200
        assert c.get("/api/soma").status_code == 401
        assert c.get("/api/soma", headers={"Authorization": "Bearer psy-token"}).status_code == 200


def test_viewer_renders_on_the_shell():
    with client(Body()) as c:
        page = c.get("/viewer").text
    assert "Seren<b>Psycheion</b>" in page and 'id="console"' in page and "loadTouch" in page
    assert "no camera button" in page


def test_root_is_the_family_info_route():
    body = Body()
    cfg = PsycheionConfig(soma=SomaConfig(robot=Endpoint(url="http://bot:8080", token=ROBOT_TOKEN),
                                          voice=Endpoint(url="http://voice:7480", token=VOICE_TOKEN)))
    cfg.updates.enabled = False
    app = create_app(cfg, robot_transport=httpx.MockTransport(body.robot), voice_transport=httpx.MockTransport(body.voice))
    with TestClient(app) as c:
        r = c.get("/")
    info = r.json()
    assert r.status_code == 200 and info["service"] == "seren-psycheion" and info["viewer"] == "/viewer"
    assert set(info["updates"]) == {"status", "distribution", "installed", "latest", "update_available", "detail", "checked_at"}
    assert info["updates"]["status"] == "disabled"


def test_no_route_takes_a_photo():
    with client(Body()) as c:
        paths = {route.path for route in c.app.routes}
    assert not any("photo" in p or "camera" in p for p in paths), paths


def test_token_file_and_config(tmp_path, monkeypatch):
    tok = tmp_path / "control_token"
    tok.write_text("from-a-file\n", encoding="utf-8")
    assert Endpoint(token_file=str(tok)).resolve() == "from-a-file"
    assert Endpoint(token_file=str(tmp_path / "missing"), token="inline").resolve() == "inline"
    yml = tmp_path / "p.yaml"
    yml.write_text("soma:\n  robot:\n    url: http://10.0.0.5:8080\n    token_file: " + str(tok).replace("\\", "/") + "\n",
                   encoding="utf-8")
    cfg = load_config(str(yml))
    assert cfg.port == 7429 and cfg.host == "127.0.0.1"
    assert cfg.soma.robot.url == "http://10.0.0.5:8080" and cfg.soma.robot.resolve() == "from-a-file"
    assert cfg.soma.voice.url == "http://127.0.0.1:7480"


def test_a_rotated_token_is_picked_up_from_its_file_without_a_restart(tmp_path):
    f = tmp_path / "control_token"
    f.write_text("old")
    seen = []

    def robot(request):
        seen.append(request.headers.get("X-StackChan-Token"))
        if request.headers.get("X-StackChan-Token") != "new":
            return httpx.Response(401, json={"error": "bad token"})
        return httpx.Response(200, json=STATUS)

    cfg = PsycheionConfig(server=ServerConfig(host="127.0.0.1", port=7429),
                          soma=SomaConfig(robot=Endpoint(url="http://bot:8080", token_file=str(f)),
                                          voice=Endpoint(url="http://voice:7480", token=VOICE_TOKEN)))
    app = create_app(cfg, robot_transport=httpx.MockTransport(robot), voice_transport=httpx.MockTransport(Body().voice))
    with TestClient(app) as c:
        f.write_text("new")                  # the voice server rotated it
        r = c.get("/api/soma").json()
    assert r["robot"]["face"] == "studio" and seen == ["old", "new"]


def test_no_robot_set_up_says_so():
    cfg = PsycheionConfig(server=ServerConfig(host="127.0.0.1", port=7429),
                          soma=SomaConfig(robot=Endpoint(url=""), voice=Endpoint(url="http://voice:7480", token=VOICE_TOKEN)))
    with TestClient(create_app(cfg, voice_transport=httpx.MockTransport(Body().voice))) as c:
        r = c.get("/api/soma").json()
    assert r["robot"] is None and "set up yet" in r["robot_error"] and r["voice"]["ok"]
    assert load_config("does-not-exist.yaml").soma.robot.url == ""


def test_building_the_body_is_somas_not_ours():
    # Setting the robot up and its firmware live in SerenSoma's own page (8 Nov 2026):
    # Soma builds the body, Psycheion is its faces and its touch.
    with client(Body()) as c:
        paths = {route.path for route in c.app.routes}
    assert not any(p.startswith(("/api/soma/setup", "/api/soma/card", "/api/soma/firmware")) for p in paths), paths
