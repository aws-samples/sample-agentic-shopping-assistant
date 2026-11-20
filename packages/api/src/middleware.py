"""Bound request bodies before JSON parsing, including chunked uploads."""
from starlette.responses import JSONResponse


class BodyLimit:
    def __init__(self, app, max_bytes=32768):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                body = {"detail": {"code": "invalid_request", "message": "Request body too large"}}
                return await JSONResponse(body, 413)(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body"):
                break
        async def replay():
            return chunks.pop(0) if chunks else await receive()
        await self.app(scope, replay, send)
