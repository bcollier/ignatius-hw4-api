"""A limit on the size of every request body, applied before anything reads it.

FastAPI parses a multipart upload (and spools it to disk) before a route or its sign-in
check runs, so a per-file check inside the route comes too late to stop a huge upload.
This refuses a request whose declared length is too large at once, and stops one that
sends more than it declared, or streams without declaring, as soon as it passes the limit.
"""

import json

# A retreat can come with a document and a photo, each up to the upload limit.
UPLOAD_FILES = 2
SLACK = 1024 * 1024  # form fields and multipart headers


class BodyLimit:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._too_large(send)
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise TooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except TooLarge:
            await self._too_large(send)

    async def _too_large(self, send):
        body = json.dumps({"error": {"status": 413, "message": "That upload is too large."}}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class TooLarge(Exception):
    pass
