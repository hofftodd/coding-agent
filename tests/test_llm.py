"""The model client against the fake server, both providers."""

import threading

import pytest

from foreman.core.models import Node
from foreman.llm import Aborted, LLMError, stream_chat
from tests.fakellm import FakeLLM, last_user_text, system_text


@pytest.fixture
def llm():
    server = FakeLLM(
        host="127.0.0.1",
        planner=lambda req: f"echo: {last_user_text(req)} / sys: {system_text(req)}",
    ).start()
    yield server
    server.stop()


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_stream_chat(llm, provider):
    base = llm.url if provider == "openai" else llm.url.removesuffix("/v1")
    node = Node(name="n", provider=provider, base_url=base, api_key="k")
    chunks: list[str] = []
    result = await stream_chat(
        node,
        "claude-opus-5-5" if provider == "anthropic" else "m",
        "SYSTEM",
        [{"role": "user", "content": "hello there"}],
        on_text=chunks.append,
    )
    assert result.text == "echo: hello there / sys: SYSTEM"
    assert "".join(chunks) == result.text and len(chunks) > 1
    assert result.output_tokens == 5


async def test_anthropic_request_shape(llm):
    node = Node(
        name="claude", provider="anthropic", base_url=llm.url.removesuffix("/v1"), api_key="k"
    )
    await stream_chat(node, "claude-opus-5-5", "S", [{"role": "user", "content": "x"}])
    req = llm.requests[-1]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["fallbacks"] == "default"
    assert req["system"] == "S" and req["stream"] is True


async def test_abort_closes_the_stream(llm):
    llm.planner = lambda req: "x" * 5000
    llm.chunk_delay = 0.05
    node = Node(name="n", base_url=llm.url)
    stop = threading.Event()
    seen: list[str] = []

    def on_text(t: str) -> None:
        seen.append(t)
        if len(seen) == 3:
            stop.set()

    with pytest.raises(Aborted):
        await stream_chat(
            node,
            "m",
            "S",
            [{"role": "user", "content": "go"}],
            on_text=on_text,
            should_abort=stop.is_set,
        )
    assert llm.disconnected.wait(5)


async def test_http_error_is_reported():
    node = Node(name="dead", base_url="http://127.0.0.1:9/v1")
    with pytest.raises(Exception) as e:
        await stream_chat(node, "m", "S", [{"role": "user", "content": "x"}])
    assert not isinstance(e.value, Aborted)


async def test_unknown_provider():
    with pytest.raises(LLMError):
        await stream_chat(Node(name="x", provider="nope", base_url="x"), "m", "S", [])


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_list_models(llm, provider):
    from foreman.llm import list_models

    base = llm.url if provider == "openai" else llm.url.removesuffix("/v1")
    assert await list_models(Node(name="n", provider=provider, base_url=base, api_key="k")) == [
        "fake-coder"
    ]
