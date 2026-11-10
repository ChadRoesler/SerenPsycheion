"""A face's build-ups: counted on the robot's clock from the voice server's feed, fired on the robot,
never by accident. The counter's own tests came over from SerenSoma's overwhelm with it."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi.testclient import TestClient

from seren_meninges import ServerConfig
from seren_psycheion import buildups as bu
from seren_psycheion.app import create_app
from seren_psycheion.config import Endpoint, PsycheionConfig, SomaConfig


# -- the counter (from SerenSoma's overwhelm, 7 Nov 2026) ----------------------------------------

def test_a_buildup_is_earned():
    o = bu.Counter()
    # quick scratches, one a second for 20 s: it tips over once, at 15
    fired = [t for t in range(21) if o.touch(t * 1000, t * 1000 + 200, True)]
    assert fired == [15], fired
    # straight back to it: resting, nothing
    assert not any(o.touch(t * 1000, t * 1000 + 200, True) for t in range(21, 60))
    # a pause longer than the gap starts the count again
    o = bu.Counter()
    assert not any(o.touch(t * 1000, t * 1000 + 200, True) for t in list(range(0, 10)) + list(range(14, 26)))
    # one long unbroken scratch, still going: carries the run to 15 s
    o = bu.Counter()
    assert not o.touch(0, 2000, True)
    assert not o.touch(2500, 9000, True, still_going=True)
    assert o.touch(2500, 15500, True, still_going=True)
    # a hand still on the head can't start a run by itself
    o = bu.Counter()
    assert not o.touch(0, 30000, True, still_going=True) and not o.running
    # scratch, then pet: the pet ends it
    o = bu.Counter()
    assert not o.touch(0, 8000, True)
    assert not o.touch(9000, 9700, False) and not o.running


def test_a_real_19_second_scratch_gets_there():
    # A second run on 7 Nov, on the sensor's clock: unbroken from 400755 to 419723, 19 s. Every bit
    # of it the voice server labels scratching.
    run = [(400755, 50), (402120, 110), (404837, 410), (405296, 4627), (409930, 4705), (415655, 651),
           (416655, 400), (417056, 1969)]
    o = bu.Counter()
    assert [s for s, ms in run if o.touch(s, s + ms, True)], "19 s of scratching gets there at 15"


def test_a_counter_takes_its_buildups_timing():
    o = bu.counter_for(NS(seconds=5, gap=3, rest=120))
    assert [t for t in range(4) if o.touch(t * 1000, t * 1000 + 200, True)] == []
    assert 0.6 < o.progress() < 0.7          # 3.2 s of 5
    assert o.touch(5000, 5200, True)
    assert o.progress() == 0.0               # it fired: the run starts over
    assert o.resting_ms() == 120000


def test_work_hours():
    at = lambda s: time.strptime(s, "%Y-%m-%d %H:%M")
    assert bu.work_hours(at("2026-11-06 10:00"))           # a Friday morning
    assert not bu.work_hours(at("2026-11-06 19:00"))       # Friday night
    assert not bu.work_hours(at("2026-11-07 10:00"))       # Saturday
    assert bu.work_hours(at("2026-11-07 10:00"), 9, 12, (5,))


# -- what each one counts --------------------------------------------------------------------------

def B(**kw):
    base = dict(touch="scratching", seconds=15, gap=3, rest=120, pose="surprised", holdMs=4000,
                after=None, afterMs=None, flash=None, sound=None)
    return NS(**{**base, **kw})


def test_scratching_counts_what_the_voice_server_labelled_and_a_kind_counts_its_kind():
    assert bu.counts(B(), {"kind": "stroke", "scratching": True})
    assert not bu.counts(B(), {"kind": "scritch", "scratching": False})
    assert bu.counts(B(touch="hand"), {"kind": "hand"}) and not bu.counts(B(touch="hand"), {"kind": "stroke"})


def test_the_feed_fills_a_buildup_and_screen_taps_never_count():
    r = bu.Buildups(None, None)
    r.set_face({"tip": B(seconds=5)})
    taps = [{"kind": "touch_spot", "t_ms": t * 1000, "ms": 0} for t in range(10)]
    assert r.feed(taps) == []
    scratches = [{"kind": "scritch", "scratching": True, "t_ms": t * 1000, "ms": 200} for t in range(7)]
    assert r.feed(scratches) == ["tip"]


def test_a_hand_still_down_carries_a_run_on():
    r = bu.Buildups(None, None)
    r.set_face({"tip": B(seconds=10)})
    assert r.feed([{"kind": "scritch", "scratching": True, "t_ms": 0, "ms": 2000}]) == []
    assert r.feed([], NS(down=True, from_ms=2500, to_ms=8000)) == []
    assert r.feed([], NS(down=True, from_ms=2500, to_ms=10500)) == ["tip"]


def test_a_robot_reboot_starts_the_counts_over():
    r = bu.Buildups(None, None)
    r.set_face({"tip": B(seconds=10)})
    r.feed([{"kind": "scritch", "scratching": True, "t_ms": 500000 + t * 1000, "ms": 200} for t in range(8)])
    assert r.counters["tip"].running
    r.feed([{"kind": "scritch", "scratching": True, "t_ms": 3000, "ms": 200}])   # its clock went back
    assert r.counters["tip"].since == 3000


def test_a_refreshed_face_keeps_a_running_count_unless_the_buildup_changed():
    r = bu.Buildups(None, None)
    r.set_face({"tip": B(seconds=10)})
    r.feed([{"kind": "scritch", "scratching": True, "t_ms": 0, "ms": 200}])
    r.set_face({"tip": B(seconds=10)})
    assert r.counters["tip"].running
    r.set_face({"tip": B(seconds=20)})
    assert not r.counters["tip"].running


# -- firing, and the guards ------------------------------------------------------------------------

class FakeRobot:
    def __init__(self, buildups=None):
        self.calls = []
        self._buildups = buildups or {}

    async def emotion(self, name, rest=False):
        self.calls.append(("emotion", name, rest))

    async def leds(self, frame):
        self.calls.append(("leds", frame[0]))

    async def sound(self, name):
        self.calls.append(("sound", name))

    async def touch_spots(self):
        return NS(buildups=self._buildups)


class FakeVoice:
    def __init__(self, call_open=False, days=(0, 1, 2, 3, 4), start=8, end=17, feed=None):
        self.call_open = call_open
        self.wh = NS(days=list(days), start=start, end=end)
        self.feeds = list(feed or [])

    async def settings(self):
        return NS(work_hours=self.wh)

    async def status(self):
        return NS(device=self.call_open)

    async def touch(self, after=0):
        return self.feeds.pop(0) if self.feeds else NS(events=[], last=after, hand=NS(down=False, from_ms=None, to_ms=None))


async def _no_wait(*_):
    await asyncio.sleep(0)


def _runner(robot=None, voice=None, saturday=True, **kw):
    when = time.strptime("2026-11-07 21:00" if saturday else "2026-11-06 10:00", "%Y-%m-%d %H:%M")
    return bu.Buildups(robot or FakeRobot(), voice or FakeVoice(), local=lambda: when, sleep=_no_wait, **kw)


def test_it_fires_pose_flash_sound_hold_after_and_back_in_that_order():
    robot = FakeRobot()
    r = _runner(robot)
    r.set_face({"tip": B(after="relaxed", afterMs=5000, flash=[168, 0, 111], sound="gasp")})
    asyncio.run(r.fire("tip"))
    assert robot.calls == [("emotion", "surprised", True),
                           ("leds", (168, 0, 111)), ("leds", (0, 0, 0)), ("leds", (168, 0, 111)), ("leds", (0, 0, 0)),
                           ("sound", "gasp"), ("emotion", "relaxed", False), ("emotion", "neutral", False)]
    assert r.firing is None and "tip" in r.last_fired


def test_a_step_that_fails_still_puts_the_face_back():
    class Flaky(FakeRobot):
        async def sound(self, name):
            raise httpx.ConnectError("gone")
    robot = Flaky()
    r = _runner(robot)
    r.set_face({"tip": B(sound="gasp")})
    asyncio.run(r.fire("tip"))
    assert robot.calls[-1] == ("emotion", "neutral", False) and r.firing is None


@pytest.mark.parametrize("voice, saturday, enabled, says", [
    (FakeVoice(), False, True, "work hours"),
    (FakeVoice(call_open=True), True, True, "a call is open"),
    (FakeVoice(), True, False, "switched off"),
])
def test_never_by_accident(voice, saturday, enabled, says):
    r = _runner(voice=voice, saturday=saturday, enabled=enabled)
    assert says in asyncio.run(r.why_not())


def test_a_test_ignores_the_hours_and_the_switch_but_not_a_call():
    r = _runner(saturday=False, enabled=False)
    r.set_face({"tip": B()})
    assert asyncio.run(r.why_not(test=True)) is None
    r2 = _runner(voice=FakeVoice(call_open=True))
    r2.set_face({"tip": B()})
    assert "call" in asyncio.run(r2.test("tip"))
    assert "no build-up" in asyncio.run(r2.test("nope"))


def test_a_step_reads_the_face_counts_the_feed_and_fires():
    face = {"tip": B(seconds=5)}
    scratches = [{"kind": "scritch", "scratching": True, "t_ms": t * 1000, "ms": 200} for t in range(7)]
    voice = FakeVoice(feed=[NS(events=scratches, last=7, hand=NS(down=False, from_ms=None, to_ms=None))])
    robot = FakeRobot(face)

    async def go():
        r = _runner(robot, voice)
        fired = await r.step()
        await asyncio.sleep(0)        # let the firing run
        for _ in range(20):
            await asyncio.sleep(0)
        return r, fired
    r, fired = asyncio.run(go())
    assert fired == ["tip"] and r.after_seq == 7 and ("emotion", "surprised", True) in robot.calls


def test_one_that_fills_in_work_hours_says_why_it_didnt_fire():
    scratches = [{"kind": "scritch", "scratching": True, "t_ms": t * 1000, "ms": 200} for t in range(7)]
    voice = FakeVoice(feed=[NS(events=scratches, last=7, hand=NS(down=False, from_ms=None, to_ms=None))])
    r = _runner(FakeRobot({"tip": B(seconds=5)}), voice, saturday=False)
    assert asyncio.run(r.step()) == [] and r.last_skipped["tip"] == "it's work hours"


# -- the routes ------------------------------------------------------------------------------------

def test_the_routes(monkeypatch):
    cfg = PsycheionConfig(server=ServerConfig(host="127.0.0.1", port=7429),
                          soma=SomaConfig(robot=Endpoint(url="http://bot:8080", token="t"),
                                          voice=Endpoint(url="http://voice:7480", token="v")))
    with TestClient(create_app(cfg, run_buildups=False)) as c:
        c.app.state.buildups.set_face({"tip": B()})
        listing = c.get("/api/buildups").json()
        off = c.post("/api/buildups", json={"enabled": False}).json()
        bad = c.post("/api/buildups", json={"enabled": "yes"})
        missing = c.post("/api/buildups/nope/test")
    assert listing["buildups"][0]["name"] == "tip" and listing["buildups"][0]["progress"] == 0
    assert off["enabled"] is False and bad.status_code == 400 and missing.status_code == 404
