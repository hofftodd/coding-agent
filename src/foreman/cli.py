"""`foreman` command line.

foreman up            run api + orchestrator + supervisor (separate processes)
foreman api           HTTP API and dashboard
foreman orchestrator  scheduler and task runner
foreman supervisor    stop / E-stop enforcement
foreman build-image   build the executor base image (--with-aider adds Aider)
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

from foreman.config import get_settings


def _api() -> None:
    import uvicorn

    from foreman.api.app import create_app

    s = get_settings()
    uvicorn.run(create_app(), host=s.host, port=s.port, log_level="info")


def _orchestrator() -> None:
    from foreman.orchestrator.main import main

    main()


def _supervisor() -> None:
    from foreman.supervisor.main import main

    main()


def _up() -> None:
    """Run the three components as independent OS processes.

    They are separate on purpose: a hung orchestrator must not be able to block
    the supervisor from executing a Stop.
    """
    procs = {
        name: subprocess.Popen([sys.executable, "-m", "foreman.cli", name])
        for name in ("supervisor", "orchestrator", "api")
    }

    def shutdown(*_: object) -> None:
        for p in procs.values():
            p.terminate()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        while all(p.poll() is None for p in procs.values()):
            time.sleep(0.5)
    finally:
        shutdown()
        for p in procs.values():
            p.wait(timeout=15)
    dead = [n for n, p in procs.items() if p.returncode not in (0, -signal.SIGTERM)]
    if dead:
        sys.exit(f"component(s) exited unexpectedly: {', '.join(dead)}")


def _build_image() -> None:
    root = Path(__file__).resolve().parents[2]
    ctx = root / "docker" / "executor-base"
    tag = get_settings().executor_image
    extra = ["--build-arg", "WITH_AIDER=1"] if "--with-aider" in sys.argv[2:] else []
    sys.exit(subprocess.call(["docker", "build", *extra, "-t", tag, str(ctx)]))


COMMANDS = {
    "up": _up,
    "api": _api,
    "orchestrator": _orchestrator,
    "supervisor": _supervisor,
    "build-image": _build_image,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="foreman")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--with-aider", action="store_true", help="build-image: include Aider")
    args = parser.parse_args()
    COMMANDS[args.command]()


if __name__ == "__main__":
    main()
