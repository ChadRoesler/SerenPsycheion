"""The face builder, moved in from SerenSoma: a starter face drawn from nothing builds into what the robot
loads, its sounds and build-ups are checked against the face itself, and a mistake is a FaceError that
says what to fix, not a dead process."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from seren_psycheion.faces import face as fb

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _starter(tmp_path) -> Path:
    """face.py init, then a plain PNG for every file the starter face names."""
    src = tmp_path / "my_face"
    fb.cmd_init(NS(source=str(src), force=False))
    names = set(re.findall(r"([\w.-]+\.png)", (src / "face.yaml").read_text(encoding="utf-8")))
    for n in names:
        if n == "base.png":
            Image.new("RGB", (320, 240), (20, 18, 16)).save(src / n)
        elif n == "eye_white.png":
            Image.new("RGBA", (56, 48), (250, 250, 250, 255)).save(src / n)
        elif n == "iris.png":
            Image.new("RGBA", (20, 20), (40, 90, 200, 255)).save(src / n)
        else:
            Image.new("RGBA", (40, 16), (200, 60, 60, 255)).save(src / n)
    return src


def _build(src, out, **kw):
    fb.cmd_build(NS(source=str(src), out=str(out), prefix="f", dry_run=False, **kw))
    return json.loads((out / "face.json").read_text(encoding="utf-8"))


def test_a_starter_face_builds_into_what_the_robot_loads(tmp_path):
    src = _starter(tmp_path)
    manifest = _build(src, tmp_path / "out")
    bins = sorted(p.name for p in (tmp_path / "out").glob("*.bin"))
    assert manifest["version"] == fb.MANIFEST_VERSION and len(manifest["emotions"]) >= 21
    assert "f_base.bin" in bins and all(len(n) <= fb.MAX_ASSET_NAME for n in bins)
    # an LVGL image: the converter ran (without lz4, which this needs no longer)
    assert (tmp_path / "out" / "f_base.bin").stat().st_size > 320 * 240


def test_the_build_folder_defaults_beside_the_face(tmp_path):
    src = _starter(tmp_path)
    fb.cmd_build(NS(source=str(src), out=None, prefix="f", dry_run=False))
    assert (src / "build" / "face.json").is_file()


def _ogg(path: Path, size=1200):
    """Enough like Ogg Opus for the builder's check (a real one is made by Face Studio or ffmpeg)."""
    path.write_bytes(b"OggS" + b"\x00" * 24 + b"OpusHead" + b"\x01" * (size - 36))


def test_sounds_and_buildups_are_checked_against_the_face(tmp_path, capsys):
    src = _starter(tmp_path)
    _ogg(src / "gasp.ogg")
    (src / "nope.ogg").write_bytes(b"not an ogg at all")
    with (src / "face.yaml").open("a", encoding="utf-8") as f:
        f.write('''
sounds:
  gasp: gasp.ogg
  bad: nope.ogg
buildups:
  tip:
    touch: scratching
    seconds: 10
    pose: surprised
    after: happy
    flash: "#ff00a8"
    sound: gasp
  typo:
    pose: suprised
  tickle:
    touch: tickle
    pose: happy
''')
    manifest = _build(src, tmp_path / "out")
    said = capsys.readouterr().out
    assert manifest["sounds"] == ["gasp"] and (tmp_path / "out" / "snd_gasp.ogg").is_file()
    assert manifest["buildups"]["tip"] == {"touch": "scratching", "seconds": 10.0, "gap": 3.0, "rest": 120.0,
                                           "pose": "surprised", "holdMs": 4000, "after": "happy", "afterMs": 5000,
                                           "flash": [168, 0, 111], "sound": "gasp"}
    assert set(manifest["buildups"]) == {"tip"}
    assert "isn't Ogg Opus" in said and "suprised" in said and "tickle" in said


def test_a_mistake_is_an_error_that_says_what_to_fix(tmp_path):
    with pytest.raises(fb.FaceError):
        fb.cmd_build(NS(source=str(tmp_path / "nowhere"), out=str(tmp_path / "out"), prefix="f", dry_run=False))
    with pytest.raises(fb.FaceError, match="letters and digits"):
        fb.cmd_build(NS(source=str(_starter(tmp_path)), out=str(tmp_path / "o2"), prefix="no good", dry_run=False))


def test_the_command_line(tmp_path):
    src = _starter(tmp_path)
    ok = subprocess.run([sys.executable, "-m", "seren_psycheion", "face", "build", str(src), "--out", str(tmp_path / "o")],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert (tmp_path / "o" / "face.json").is_file()
    bad = subprocess.run([sys.executable, "-m", "seren_psycheion", "face", "build", str(tmp_path / "nowhere")],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and bad.stderr.startswith("error: ")


def test_face_json_is_the_same_bytes_on_every_machine(tmp_path):
    src = _starter(tmp_path)
    _build(src, tmp_path / "out")
    assert b"\r\n" not in (tmp_path / "out" / "face.json").read_bytes()
