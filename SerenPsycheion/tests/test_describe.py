"""--describe answers with one line of JSON before anything heavy is imported."""
from __future__ import annotations

import json
import subprocess
import sys


def test_describe_is_one_line_of_json():
    out = subprocess.run([sys.executable, "-m", "seren_psycheion", "--describe"], capture_output=True, text=True, check=True)
    card = json.loads(out.stdout)
    assert card["name"] == "seren-psycheion" and card["port"] == 7429 and card["group"] == "auxiliary"
    assert out.stdout.count("\n") == 1
