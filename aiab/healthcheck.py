"""Container-local readiness probe; emits no response bodies or diagnostics."""

from __future__ import annotations

import json
import os
import urllib.request


def main() -> None:
    try:
        port = int(os.getenv("LISTEN_PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/aiab/health", timeout=3) as response:
            if response.status != 200 or json.load(response).get("status") != "ready":
                raise ValueError
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
