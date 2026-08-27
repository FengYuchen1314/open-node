from pathlib import Path, PurePosixPath

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class FrontendFiles(StaticFiles):
    """Serve the built SPA without turning missing API/assets into HTML successes."""

    def __init__(self, directory, api_prefix):
        super().__init__(directory=directory, html=True, follow_symlink=False)
        if not (Path(self.directory) / "index.html").is_file():
            raise ValueError("Frontend build is missing index.html")
        self.reserved = {"api", "assets", "healthz", "docs", "redoc", "openapi.json"}
        self.reserved.add(api_prefix.strip("/").split("/", 1)[0])

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await super().__call__(scope, receive, send)

    async def get_response(self, path, scope):
        parts = PurePosixPath(path).parts
        if any(part.startswith(".") for part in parts):
            raise HTTPException(404)
        if parts and parts[0] in self.reserved - {"assets"}:
            raise HTTPException(404)
        is_index = path in {".", "", "index.html"}
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            accept = Headers(scope=scope).get("accept", "")
            html = any(item.split(";", 1)[0].strip() == "text/html" for item in accept.split(","))
            if (
                exc.status_code != 404
                or not html
                or not parts
                or parts[0] in self.reserved
                or PurePosixPath(path).suffix
            ):
                raise
            response = await super().get_response("index.html", scope)
            is_index = True
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if is_index or response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        elif parts and parts[0] == "assets":
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
