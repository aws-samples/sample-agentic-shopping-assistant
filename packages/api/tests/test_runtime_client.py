import asyncio
from types import SimpleNamespace

import httpx
import pytest
from config import get_config
from runtime_client import RuntimeClient


@pytest.mark.asyncio
async def test_bearer_transport_escapes_arn_and_streams(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/example")
    get_config.cache_clear()
    seen = []
    async def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b'data: {"version": 1, "type": "textDelta", "data": {"text": "hi"}}\n\n')
    client = RuntimeClient(httpx.MockTransport(handler))
    chunks = [c async for c in client.invoke_stream("hello", "s" * 97, "Bearer ACCESS")]
    assert b"hi" in b"".join(chunks)
    assert seen[0].headers["authorization"] == "Bearer ACCESS"
    assert "%3A" in str(seen[0].url)
    assert seen[0].headers["x-amzn-bedrock-agentcore-runtime-session-id"] == "s" * 97
    await client.close()
    get_config.cache_clear()


@pytest.mark.asyncio
async def test_only_the_invocation_deadline_bounds_a_quiet_stream(monkeypatch):
    # The agent sends nothing while the model or a tool runs, often for over a minute.
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/example")
    get_config.cache_clear()
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200)
    client = RuntimeClient(httpx.MockTransport(handler))
    _ = [c async for c in client.invoke_stream("hello", "s" * 97, "Bearer ACCESS")]
    await client.stop_session("s" * 97, "Bearer ACCESS")
    invocation, stop = seen
    assert invocation.extensions["timeout"] == {"connect": 5, "read": None, "write": 10, "pool": 5}
    # Calls that answer at once keep their read timeout.
    assert stop.extensions["timeout"] == {"connect": 5, "read": 60, "write": 10, "pool": 5}
    await client.close()
    get_config.cache_clear()


@pytest.mark.asyncio
async def test_does_not_retry_failed_invocation():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429)
    client = RuntimeClient(httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        _ = [c async for c in client.invoke_stream("hello", "s" * 97, "Bearer ACCESS")]
    assert len(calls) == 1
    await client.close()


def slow_runtime(delay):
    async def body():
        yield b'data: {"version": 1, "type": "textDelta", "data": {"text": "a"}}\n\n'
        await asyncio.sleep(delay)
        yield b'data: {"version": 1, "type": "done", "data": {"stopReason": "end_turn"}}\n\n'

    return httpx.MockTransport(lambda request: httpx.Response(200, content=body()))


def with_deadline(client, seconds):
    client._config = SimpleNamespace(request_timeout=seconds, qualifier="DEFAULT")
    return client


@pytest.mark.asyncio
async def test_the_invocation_deadline_bounds_the_wait_for_the_runtime(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/example")
    get_config.cache_clear()
    client = with_deadline(RuntimeClient(slow_runtime(5)), 0.1)
    chunks = client.invoke_stream("hello", "s" * 97, "Bearer ACCESS")
    assert b"textDelta" in await anext(chunks)
    with pytest.raises(TimeoutError):
        await anext(chunks)
    await client.close()
    get_config.cache_clear()


@pytest.mark.asyncio
async def test_the_deadline_never_cancels_the_caller_between_chunks(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/example")
    get_config.cache_clear()
    client = with_deadline(RuntimeClient(slow_runtime(0)), 0.1)
    chunks = client.invoke_stream("hello", "s" * 97, "Bearer ACCESS")
    await anext(chunks)
    # The caller is busy (say, writing to a slow browser) past the deadline: its own await is
    # left alone, and the deadline applies at its next wait on the Runtime.
    await asyncio.sleep(0.2)
    with pytest.raises(TimeoutError):
        await anext(chunks)
    await client.close()
    get_config.cache_clear()
