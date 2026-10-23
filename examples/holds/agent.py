#!/usr/bin/env python3
"""Deterministic agent for the sample Holds suite.

Holds writes the task input to HOLDS_INPUT_PATH and expects a JSON object at
HOLDS_RESULT_PATH. This agent classifies one customer message. It does not
import Holds, Vigil, or Maul.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    payload = json.loads(Path(os.environ["HOLDS_INPUT_PATH"]).read_text(encoding="utf-8"))
    message = str(payload.get("customer_message", "")).lower()
    category = "refund_request" if "charged twice" in message or "refund" in message else "other"
    result = {"category": category, "approved_refund": False}
    destination = Path(os.environ["HOLDS_RESULT_PATH"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
