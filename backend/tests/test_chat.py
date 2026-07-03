def test_chat_completions_mock(client, auth_headers):
    response = client.post(
        "/api/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "mock/spice-sibyl-1",
            "messages": [{"role": "user", "content": "ciao"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["role"] == "assistant"


def test_chat_requires_auth(client):
    response = client.post(
        "/api/v1/chat/completions",
        json={
            "model": "mock/spice-sibyl-1",
            "messages": [{"role": "user", "content": "ciao"}],
            "stream": False,
        },
    )
    assert response.status_code == 401


def test_tool_loop_provider_http_error_reaches_stream():
    """A provider 429 in the tool loop must become an SSE error frame, not crash."""
    import asyncio
    import json as _json

    import httpx

    from app.schemas.chat import ChatCompletionRequest
    from app.services.chat_service import ChatService

    class RateLimitedProvider:
        async def complete(self, req):
            request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
            response = httpx.Response(
                429,
                request=request,
                json={"status": 429, "title": "Too Many Requests"},
            )
            raise httpx.HTTPStatusError("429", request=request, response=response)

    req = ChatCompletionRequest(
        model="nvidia/some-model",
        messages=[{"role": "user", "content": "ciao"}],
        stream=True,
        tools=[{"type": "function", "function": {"name": "noop", "description": "noop", "parameters": {}}}],
    )

    async def run():
        frames = []
        async for frame in ChatService()._stream_with_tools(RateLimitedProvider(), req):
            frames.append(frame)
        return frames

    frames = asyncio.run(run())
    events = [f["event"] for f in frames]
    assert "error" in events
    assert events[-1] == "done"
    err = _json.loads(next(f["data"] for f in frames if f["event"] == "error"))
    assert err["status"] == 429
    assert "429" in err["message"]
    assert "Too Many Requests" in err["message"]
