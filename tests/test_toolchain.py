import pytest

from foreman.toolchain import DOCKERFILE, ToolchainError, _copy_sources, check_dockerfile, image_tag


def test_must_build_from_the_executor_base():
    check_dockerfile("FROM foreman/executor-base:latest\nRUN true\n")
    check_dockerfile(
        "FROM node:22 AS build\nRUN x\nFROM foreman/executor-base\nCOPY --from=build /a /a\n"
    )
    with pytest.raises(ToolchainError, match="must build FROM foreman/executor-base"):
        check_dockerfile("FROM ubuntu:24.04\n")
    with pytest.raises(ToolchainError, match="no FROM"):
        check_dockerfile("RUN true\n")


def test_copy_sources():
    text = (
        "FROM foreman/executor-base\n"
        "COPY requirements.txt /tmp/\n"
        "COPY --chown=1000 pyproject.toml uv.lock /app/\n"
        "COPY --from=build /x /y\n"
        "ADD [\"setup.cfg\", \"/app/\"]\n"
    )
    assert _copy_sources(text) == ["requirements.txt", "pyproject.toml", "uv.lock", "setup.cfg"]


def test_image_tag_tracks_dockerfile_and_copied_files(tmp_path):
    (tmp_path / ".foreman").mkdir()
    df = tmp_path / DOCKERFILE
    df.write_text("FROM foreman/executor-base\nCOPY requirements.txt /tmp/\n")
    (tmp_path / "requirements.txt").write_text("httpx\n")
    (tmp_path / "unrelated.py").write_text("x = 1\n")
    a = image_tag(1, tmp_path)
    (tmp_path / "unrelated.py").write_text("x = 2\n")
    assert image_tag(1, tmp_path) == a  # unrelated changes don't rebuild
    (tmp_path / "requirements.txt").write_text("httpx\nrich\n")
    b = image_tag(1, tmp_path)
    assert b != a  # a dependency change does
    df.write_text(df.read_text() + "RUN true\n")
    assert image_tag(1, tmp_path) != b
    assert a.startswith("foreman/toolchain-")
