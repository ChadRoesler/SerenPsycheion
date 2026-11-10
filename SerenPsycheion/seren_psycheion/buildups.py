"""A face's build-ups, run here: the face declares them (face.yaml buildups:), the robot carries them
(GET /api/touch-spots), the voice server says what the hand is doing (GET /touch), and this counts.

A build-up is a touch kept up: `seconds` of it, with no pause longer than `gap`, tips the face into
`pose` (resting in it, so nothing knocks it out early), flashes the rails, plays the face's own
`sound` on the robot, holds `holdMs`, settles through `after` for `afterMs`, and goes back. Then it
rests `rest` seconds. It's counted on the robot's own clock, so one long unbroken scratch counts all
the way through: the voice server's feed says which head touches were part of scratching, and which
hand is still down right now.

Earned, never by accident. Not in the house's work hours (the voice server's settings), not with a
call open, and not when it's switched off here. Test fires one by hand, whatever the hours.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger("seren_psycheion.buildups")

# Touches on top of the head; a tap on the screen is something else and never counts.
HEAD_KINDS = ("stroke", "poke", "scritch", "hand", "finger_rest")


@dataclass
class Counter:
    """One build-up's count, on the sensor's clock. Came from SerenSoma's overwhelm (7 Nov 2026),
    where it was tuned against a real hand: 15 s of quick scratches tips once, a pause past the gap
    starts over, a pet ends it, and a hand that hasn't lifted carries a run on but can't start one."""
    after_ms: float = 15000
    gap_ms: float = 3000
    rest_ms: float = 120000
    since: Optional[float] = None
    last: float = -1e12
    fired: float = -1e12

    def touch(self, start_ms, end_ms, counts, still_going=False) -> bool:
        """A touch from start_ms to end_ms. counts: it's the kind this build-up counts. still_going: the
        hand hasn't lifted yet. True when it's time to tip over."""
        if self.since is not None and (start_ms - self.last > self.gap_ms or not counts):
            self.since = None              # a pause, or the wrong kind of touch: it starts over
        if self.since is None:
            if not counts or still_going:
                return False
            self.since = start_ms
        self.last = max(self.last, end_ms)
        if self.last - self.since >= self.after_ms and self.last - self.fired >= self.rest_ms:
            self.fired = self.last
            self.since = None
            return True
        return False

    @property
    def running(self) -> bool:
        return self.since is not None

    def progress(self) -> float:
        """How far along a run is, 0..1: what the console's meter shows."""
        return min(1.0, (self.last - self.since) / self.after_ms) if self.running else 0.0

    def resting_ms(self) -> float:
        """How much longer it rests after it last fired, on the sensor's clock (0: ready)."""
        return max(0.0, self.rest_ms - (self.last - self.fired))


def counter_for(b: Any) -> Counter:
    return Counter(after_ms=float(b.seconds) * 1000, gap_ms=float(b.gap) * 1000, rest_ms=float(b.rest) * 1000)


def counts(b: Any, event: dict) -> bool:
    """Whether a head touch is the kind this build-up counts."""
    if b.touch == "scratching":
        return bool(event.get("scratching"))
    return event.get("kind") == b.touch


def work_hours(local, start=8, end=17, days=(0, 1, 2, 3, 4)) -> bool:
    """local: a time.struct_time. True on one of days (Monday is 0) inside [start, end)."""
    return local.tm_wday in days and start <= local.tm_hour < end


class Buildups:
    """Watches the feed, counts every build-up the face on the robot declares, and fires them.

    robot: a seren_sinew SomaRobot; voice: a SomaVoice. clock/local/sleep are there for the tests."""

    FACE_EVERY_S = 30.0       # how often to look at what the face declares again (a new face, a reboot)
    HOUSE_EVERY_S = 60.0      # how often to read the work hours again

    def __init__(self, robot, voice, *, enabled: bool = True, poll: float = 1.0,
                 clock: Callable[[], float] = time.monotonic, local: Callable[[], Any] = time.localtime,
                 sleep=asyncio.sleep):
        self.robot, self.voice = robot, voice
        self.enabled = enabled
        self.poll = poll
        self.clock, self.local, self.sleep = clock, local, sleep
        self.face: dict[str, Any] = {}
        self.counters: dict[str, Counter] = {}
        self.after_seq = 0
        self.last_t_ms = -1
        self.firing: Optional[str] = None
        self.last_fired: dict[str, float] = {}       # wall time, for the console
        self.last_skipped: dict[str, str] = {}       # why the last one that filled didn't fire
        self.face_at = -1e12
        self.house: dict[str, Any] = {}
        self.house_at = -1e12
        self.errors = 0

    # -- what the face says --------------------------------------------------------------------------

    def set_face(self, buildups: Optional[dict]) -> None:
        """The face's build-ups. A counter is kept across a refresh when its build-up hasn't changed."""
        buildups = buildups or {}
        kept = {}
        for name, b in buildups.items():
            old = self.face.get(name)
            same = old is not None and (old.touch, old.seconds, old.gap, old.rest) == (b.touch, b.seconds, b.gap, b.rest)
            kept[name] = self.counters[name] if same and name in self.counters else counter_for(b)
        self.face, self.counters = dict(buildups), kept

    async def refresh_face(self) -> None:
        spots = await self.robot.touch_spots()
        self.set_face(spots.buildups)
        self.face_at = self.clock()

    # -- counting ------------------------------------------------------------------------------------

    def feed(self, events: list, hand: Any = None) -> list[str]:
        """One look at the feed: its new events and the hand that's down. Returns the build-ups that
        filled, in the order they did."""
        filled = []
        for e in events:
            if e.get("kind") not in HEAD_KINDS:
                continue
            t_ms = int(e.get("t_ms") or 0)
            if t_ms < self.last_t_ms - 60000:       # the robot's clock went back: it rebooted
                self.counters = {n: counter_for(b) for n, b in self.face.items()}
            self.last_t_ms = t_ms
            for name, c in self.counters.items():
                if c.touch(t_ms, t_ms + int(e.get("ms") or 0), counts(self.face[name], e)):
                    filled.append(name)
        if hand is not None and getattr(hand, "down", False) and hand.from_ms is not None:
            for name, c in self.counters.items():
                if c.touch(hand.from_ms, hand.to_ms, True, still_going=True):
                    filled.append(name)
        return filled

    # -- the guards ----------------------------------------------------------------------------------

    async def why_not(self, test: bool = False) -> Optional[str]:
        """Why a build-up that filled mustn't fire now, or None. Test ignores the hours and the switch."""
        if self.firing:
            return f"{self.firing} is already going"
        if not test and not self.enabled:
            return "build-ups are switched off"
        if not test:
            if self.clock() - self.house_at > self.HOUSE_EVERY_S:
                s = await self.voice.settings()
                wh = s.work_hours
                self.house = {"days": list(wh.days), "start": wh.start, "end": wh.end}
                self.house_at = self.clock()
            h = self.house
            if h and work_hours(self.local(), h["start"], h["end"], tuple(h["days"])):
                return "it's work hours"
        st = await self.voice.status()
        if st.device:
            return "a call is open"
        return None

    # -- firing --------------------------------------------------------------------------------------

    async def fire(self, name: str) -> None:
        """The build-up's moment: pose (resting in it), the flash, the sound, the hold, the after, back."""
        b = self.face[name]
        self.firing = name
        self.last_fired[name] = time.time()
        log.info("build-up %s: tipping into %s", name, b.pose)
        try:
            await self._quietly(self.robot.emotion(b.pose, rest=True))
            if b.flash:
                for _ in range(2):
                    await self._quietly(self.robot.leds([tuple(b.flash)] * 12))
                    await self.sleep(0.15)
                    await self._quietly(self.robot.leds([(0, 0, 0)] * 12))
                    await self.sleep(0.1)
            if b.sound:
                await self._quietly(self.robot.sound(b.sound))
            await self.sleep(b.holdMs / 1000)
            if b.after:
                await self._quietly(self.robot.emotion(b.after))     # and the resting pose goes with it
                await self.sleep((b.afterMs or 0) / 1000)
        finally:
            await self._quietly(self.robot.emotion("neutral"))          # clears the resting pose either way
            self.firing = None

    async def _quietly(self, call) -> None:
        try:
            await call
        except Exception as e:  # noqa: BLE001 - one step not landing mustn't strand the face mid-flow
            log.warning("build-up step: %s", e)

    async def test(self, name: str) -> Optional[str]:
        """Fire one by hand. Returns why not, or None when it's on its way."""
        if name not in self.face:
            return "this face has no build-up by that name"
        why = await self.why_not(test=True)
        if why:
            return why
        asyncio.ensure_future(self.fire(name))
        return None

    # -- the loop ------------------------------------------------------------------------------------

    async def step(self) -> list[str]:
        """One pass: the face if it's time, the feed, and whatever filled. Returns what fired."""
        if self.clock() - self.face_at > self.FACE_EVERY_S:
            await self.refresh_face()
        feed = await self.voice.touch(after=self.after_seq)
        if feed.last < self.after_seq:            # the voice server restarted: its count did too
            self.after_seq = 0
            return []
        self.after_seq = feed.last
        fired = []
        for name in self.feed([vars(e) if not isinstance(e, dict) else e for e in feed.events], feed.hand):
            why = await self.why_not()
            if why:
                self.last_skipped[name] = why
                log.info("build-up %s filled, not fired: %s", name, why)
                continue
            asyncio.ensure_future(self.fire(name))
            fired.append(name)
        return fired

    async def run(self) -> None:
        while True:
            try:
                await self.step()
                self.errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - the body being away is normal; keep looking
                self.errors += 1
                if self.errors in (1, 10, 100):
                    log.info("build-ups: can't read the body (%s)", e)
            await self.sleep(self.poll)

    def describe(self) -> dict:
        """What the console shows."""
        return {
            "enabled": self.enabled,
            "firing": self.firing,
            "buildups": [{
                "name": name, "touch": b.touch, "seconds": b.seconds, "pose": b.pose, "after": b.after,
                "sound": b.sound, "running": self.counters[name].running,
                "progress": round(self.counters[name].progress(), 3),
                "resting_s": round(self.counters[name].resting_ms() / 1000) if name in self.last_fired else 0,
                "last_fired": self.last_fired.get(name), "last_skipped": self.last_skipped.get(name),
            } for name, b in self.face.items()],
        }
