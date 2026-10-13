"""Per-project toolchain images.

A project can carry ``.foreman/Dockerfile`` (the planner writes one during
Phase 1, or you add it). It must build ``FROM`` the executor base image, so
agents keep running as the unprivileged ``agent`` user with OpenCode
installed, and adds the runtimes and dependencies the project needs.

Foreman builds it on demand, before the first step that needs it, and tags
the result by a hash of the Dockerfile and the files it COPYs/ADDs, so an
unchanged toolchain is built once and reused. The build itself has normal
network access (it installs packages); the tasks that run in the image do not.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
from collections.abc import Callable
from pathlib import Path

from foreman.config import get_settings

DOCKERFILE = ".foreman/Dockerfile"
BUILD_TIMEOUT_S = 1800

_locks: dict[str, asyncio.Lock] = {}


class ToolchainError(RuntimeError):
    pass


def _base_repo(image: str) -> str:
    """'foreman/executor-base:latest' -> 'foreman/executor-base'."""
    name = image.rsplit("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    return name.rsplit(":", 1)[0] if ":" in last else name


def check_dockerfile(text: str) -> None:
    """Every stage that ends up in the image must start from the executor base."""
    froms = [ln.split() for ln in text.splitlines() if ln.strip().upper().startswith("FROM ")]
    if not froms:
        raise ToolchainError(f"{DOCKERFILE} has no FROM line")
    final = [t for t in froms[-1][1:] if not t.startswith("--")]
    base = _base_repo(get_settings().executor_image)
    if not final or _base_repo(final[0]) != base:
        raise ToolchainError(
            f"{DOCKERFILE} must build FROM {base} (got {final[0] if final else '?'}), "
            "so agents keep running as the unprivileged agent user"
        )


def _copy_sources(text: str) -> list[str]:
    """Build-context paths referenced by COPY/ADD (excluding --from= stages)."""
    out: list[str] = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or parts[0].upper() not in ("COPY", "ADD"):
            continue
        rest = parts[1].strip()
        flags = []
        while rest.startswith("--"):
            flag, _, rest = rest.partition(" ")
            flags.append(flag)
            rest = rest.strip()
        try:
            # JSON form: COPY ["src", "dest"]; otherwise shell-style words.
            args = json.loads(rest) if rest.startswith("[") else shlex.split(rest)
        except (ValueError, json.JSONDecodeError):
            continue
        args = [*flags, *[str(a) for a in args]]
        if any(a.startswith("--from") for a in args):
            continue
        out += [a for a in args[:-1] if not a.startswith("--")]
    return out


def image_tag(project_id: int, context: Path) -> str:
    """Content-addressed tag for the project's toolchain image."""
    text = (context / DOCKERFILE).read_text()
    h = hashlib.sha256(text.encode())
    h.update(get_settings().executor_image.encode())
    for src in sorted(_copy_sources(text)):
        for path in sorted(context.glob(src)) if re.search(r"[*?\[]", src) else [context / src]:
            if path.is_file():
                h.update(path.relative_to(context).as_posix().encode())
                h.update(path.read_bytes())
    instance = get_settings().instance_id
    return f"foreman/toolchain-{instance}-{project_id}:{h.hexdigest()[:12]}"


async def _image_exists(tag: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "docker", "image", "inspect", tag,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0


async def ensure_image(
    project_id: int, context: Path, log: Callable[[str], None] | None = None
) -> str:
    """The image to run this project's steps in: its toolchain image, or "" for the
    executor base image. Builds the toolchain image if needed."""
    if get_settings().runtime != "docker" or not (context / DOCKERFILE).is_file():
        return ""
    check_dockerfile((context / DOCKERFILE).read_text())
    tag = image_tag(project_id, context)
    lock = _locks.setdefault(tag, asyncio.Lock())
    async with lock:
        if await _image_exists(tag):
            return tag
        if log:
            log(f"building toolchain image {tag} from {DOCKERFILE}")
        proc = await asyncio.create_subprocess_exec(
            "docker", "build", "--progress", "plain", "-t", tag,
            "--label", "foreman=1", "--label", f"foreman.project={project_id}",
            "-f", str(context / DOCKERFILE), str(context),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        tail: list[str] = []

        async def pump() -> int:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                tail.append(line)
                del tail[:-40]
                if log:
                    log(line)
            return await proc.wait()

        try:
            code = await asyncio.wait_for(pump(), BUILD_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise ToolchainError(f"toolchain image build timed out after {BUILD_TIMEOUT_S}s") \
                from None
        if code != 0:
            raise ToolchainError("toolchain image build failed:\n" + "\n".join(tail[-15:]))
        return tag
