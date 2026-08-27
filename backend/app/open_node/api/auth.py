from secrets import compare_digest

from fastapi import HTTPException, Request, Response

from open_node.services.auth import SessionIdentity

SESSION_COOKIE = "open_node_session"


def check_request_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if origin and origin != expected and origin not in request.app.state.settings.cors_origins:
        raise HTTPException(403, "Request origin is not allowed")


def require_administrator(request: Request, response: Response) -> SessionIdentity:
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    identity = request.app.state.auth.authenticate(
        request.cookies.get(SESSION_COOKIE),
        settings.session_idle_seconds,
    )
    if not identity:
        raise HTTPException(
            401, "Administrator sign-in required", headers={"Cache-Control": "no-store"}
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_request_origin(request)
        if not compare_digest(
            request.headers.get("x-csrf-token", "").encode(), identity.csrf_token.encode()
        ):
            raise HTTPException(403, "Invalid CSRF token")
    return identity
