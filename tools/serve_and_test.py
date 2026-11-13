"""Start the demo service on 127.0.0.1:8000, run the API suite against it over real HTTP, stop it.

Used by `make demo`. Exit code is pytest's.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000/health"


def wait_until_up(proc: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit("demo service exited during start-up")
        try:
            with urllib.request.urlopen(URL, timeout=1) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("demo service did not become healthy")


def main() -> int:
    env = {**os.environ, "PORT": "8000", "HOST": "127.0.0.1", "APP_ENV": "local"}
    server = subprocess.Popen([sys.executable, "-m", "demo_service"], env=env)
    try:
        wait_until_up(server)
        args = ["-m", "pytest", "tests/api", "--env", "local", "-p", "no:cacheprovider",
                "--junitxml", "reports/junit.xml", *sys.argv[1:]]
        return subprocess.call([sys.executable, *args])
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
