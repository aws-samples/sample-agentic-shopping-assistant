"""Bearer HTTP transport for a CUSTOM_JWT AgentCore Runtime."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from urllib.parse import quote

import httpx
from config import get_config

# For calls that answer at once, such as stopping a session.
TIMEOUT = httpx.Timeout(connect=5, read=60, write=10, pool=5)
# An invocation's stream is quiet while the model or a Gateway tool runs, often for over a minute:
# the agent drops reasoning and the AgentCore SDK sends no keepalives. So it has no read timeout,
# and the invocation deadline alone bounds the wait.
STREAM_TIMEOUT = httpx.Timeout(connect=5, read=None, write=10, pool=5)


class RuntimeClient:
    def __init__(self, transport=None):
        self._config = get_config()
        arn = self._config.agent_runtime_arn
        self._url = f"https://bedrock-agentcore.{self._config.region}.amazonaws.com/runtimes/{quote(arn, safe='')}"
        self._client = httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False, timeout=TIMEOUT,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    async def close(self):
        await self._client.aclose()

    async def invoke_stream(self, prompt: str, session_id: str, token: str) -> AsyncIterator[bytes]:
        """The Runtime's response bytes. Raises TimeoutError once REQUEST_TIMEOUT_SECONDS have passed.

        The deadline bounds every wait on the Runtime but never a yield, so the timeout can't
        cancel the caller while it is writing to the browser.
        """
        if not token or not token.startswith("Bearer "):
            raise ValueError("Bearer access token required")
        deadline = asyncio.get_running_loop().time() + self._config.request_timeout
        # No automatic retry of invocations: tools may have side effects.
        async with AsyncExitStack() as stack:
            async with asyncio.timeout_at(deadline):
                response = await stack.enter_async_context(self._client.stream(
                    "POST", f"{self._url}/invocations", params={"qualifier": self._config.qualifier},
                    headers={"Authorization": token, "Accept": "text/event-stream", "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id},
                    json={"prompt": prompt}, timeout=STREAM_TIMEOUT,
                ))
                response.raise_for_status()
            chunks = response.aiter_bytes()
            stack.push_async_callback(chunks.aclose)
            while True:
                async with asyncio.timeout_at(deadline):
                    try:
                        chunk = await anext(chunks)
                    except StopAsyncIteration:
                        return
                yield chunk

    async def stop_session(self, session_id: str, token: str):
        response = await self._client.post(
            f"{self._url}/stopruntimesession", params={"qualifier": self._config.qualifier},
            headers={"Authorization": token, "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id}, json={},
        )
        if response.status_code != 404:
            response.raise_for_status()
