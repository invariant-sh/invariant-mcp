#!/usr/bin/env python3
"""One-shot agent for `run_failure_probe` / `maul test`.

Maul injects MAUL_BASE_URL, OPENAI_BASE_URL, and MAUL_SESSION_ID. This agent
sends a single chat completion to that base URL and retries once on 429 or 500.
force_500 / force_429 never reach upstream, so this file can run without a
provider key when the Maul config uses those scenarios at probability 1.0.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def main() -> int:
    base = os.environ.get("MAUL_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not base:
        print("MAUL_BASE_URL or OPENAI_BASE_URL is required", flush=True)
        return 2
    root = base.rstrip("/")
    url = root + "/chat/completions" if root.endswith("/v1") else root + "/v1/chat/completions"
    body = json.dumps(
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Reply with the word pong."}],
        }
    ).encode()
    session = os.environ.get("MAUL_SESSION_ID", "")
    for _attempt in range(2):
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", "Bearer " + os.environ.get("OPENAI_API_KEY", "local"))
        if session:
            request.add_header("X-Maul-Session-Id", session)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
        except urllib.error.HTTPError as error:
            if error.code in {429, 500}:
                continue
            print(f"upstream HTTP {error.code}", flush=True)
            return 1
        except urllib.error.URLError as error:
            print(f"request failed: {error.reason}", flush=True)
            return 1
        else:
            return 0
    print("faulted response was not recovered", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
