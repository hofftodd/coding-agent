"""Executor containers (validation.md V4.1, V4.4, V8.1 with Docker).

Needs a Docker daemon and the foreman/executor-base image:
    foreman build-image
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

from foreman.core.models import TaskState
from foreman.orchestrator.main import Orchestrator
from foreman.sandbox import running_containers
from tests.helpers import TERMINAL, drive, make_project, make_task, task, wait_for

IMAGE = os.environ.get("FOREMAN_TEST_IMAGE", "foreman/executor-base:latest")


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return r.returncode == 0


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _docker_ready(), reason=f"docker or image {IMAGE} not available"),
]


@pytest.fixture(autouse=True)
def docker_runtime(monkeypatch):
    monkeypatch.setenv("FOREMAN_RUNTIME", "docker")
    monkeypatch.setenv("FOREMAN_EXECUTOR_IMAGE", IMAGE)
    monkeypatch.setenv("FOREMAN_GITHUB_TOKEN", "ghp_must_not_leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-leak")


PROBE = """
id -u > probe_uid
pwd > probe_pwd
env | sort > probe_env
cat /proc/mounts > probe_mounts
echo 'VALUE = 42' > src/app.py
"""


async def test_task_runs_in_isolated_container(client, origin, env):
    p = make_project(client, origin)
    t = make_task(client, p["id"], script=PROBE, validation="grep -q 42 src/app.py && id -u")
    await drive(Orchestrator(), lambda: task(t["id"]).state in TERMINAL, timeout=120)
    assert task(t["id"]).state == TaskState.DONE, task(t["id"]).reason

    from foreman import gitops

    repo = gitops.repo_dir(p["id"])
    assert (repo / "src" / "app.py").read_text() == "VALUE = 42\n"
    assert (repo / "probe_uid").read_text().strip() == "1000"  # never root
    assert (repo / "probe_pwd").read_text().strip() == "/workspace"
    env_dump = (repo / "probe_env").read_text()
    assert "must_not_leak" not in env_dump and "must-not-leak" not in env_dump  # NFR-5
    mounts = (repo / "probe_mounts").read_text()
    # Only the task clone and the read-only control dir come from the host.
    assert str(env) not in mounts
    assert " /workspace " in mounts and " /foreman " in mounts
    ctl_line = next(line for line in mounts.splitlines() if " /foreman " in line)
    assert " ro," in ctl_line or ctl_line.split()[3].startswith("ro")


def test_stop_kills_container_within_10s(client, origin):
    import sys

    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "foreman.cli", c],
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for c in ("supervisor", "orchestrator")
    ]
    try:
        p = make_project(client, origin)
        t = make_task(client, p["id"], script="sleep 300 & sleep 300 & wait")

        def container() -> str | None:
            names = [n for n, run in running_containers().items() if run]
            return names[0] if names else None

        wait_for(lambda: container() is not None, timeout=60)
        name = container()
        t0 = time.monotonic()
        client.post("/api/controls", json={"scope": "task", "target_id": t["id"], "action": "stop"})
        wait_for(lambda: name not in running_containers(), timeout=10)
        assert time.monotonic() - t0 < 10
        wait_for(lambda: task(t["id"]).state == TaskState.STOPPED_BY_USER)
    finally:
        for proc in procs:
            proc.terminate()
            proc.wait(10)


async def test_opencode_executor_against_fake_model(client, origin):
    """Real OpenCode in the executor container, driven by a scripted model server."""
    from sqlmodel import select

    from foreman.core.models import Event
    from foreman.db import session
    from tests.fakellm import MODEL, FakeLLM

    llm = FakeLLM().start()
    try:
        n = client.post(
            "/api/nodes",
            json={"name": "fake", "base_url": f"http://host.docker.internal:{llm.port}/v1"},
        ).json()
        p = make_project(
            client,
            origin,
            executor_kind="opencode",
            role_bindings={"executor": {"node_id": n["id"], "model": MODEL}},
        )
        t = make_task(
            client,
            p["id"],
            script="",
            title="Create hello.txt",
            validation="grep -q 'fake model' hello.txt",
            allowed_globs=["hello.txt"],
        )
        await drive(Orchestrator(), lambda: task(t["id"]).state in TERMINAL, timeout=180)
        with session() as s:
            events = s.exec(select(Event).where(Event.task_id == t["id"])).all()
        log = "\n".join(e.message for e in events)
        assert task(t["id"]).state == TaskState.DONE, f"{task(t['id']).reason}\n{log[-3000:]}"
        from foreman import gitops

        assert "fake model" in (gitops.repo_dir(p["id"]) / "hello.txt").read_text()
        prompts = json.dumps(llm.requests)
        assert "Create hello.txt" in prompts  # the context pack reached the model
    finally:
        llm.stop()
