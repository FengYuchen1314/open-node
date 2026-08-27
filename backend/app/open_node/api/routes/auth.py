from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from open_node.api.auth import SESSION_COOKIE, check_request_origin, require_administrator
from open_node.domain.auth import LoginRequest, PasswordChangeRequest, SessionResponse
from open_node.services.auth import SessionIdentity

router = APIRouter(prefix="/auth", tags=["authentication"])


def rate_limit(request: Request) -> None:
    peer = request.client.host if request.client else "unknown"
    if not request.app.state.auth.allow_login_attempt(peer):
        raise HTTPException(
            429, "Too many attempts; try again shortly", headers={"Retry-After": "60"}
        )


@router.get("/session", response_model=SessionResponse)
def get_session(request: Request, response: Response) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    identity = request.app.state.auth.authenticate(
        request.cookies.get(SESSION_COOKIE),
        request.app.state.settings.session_idle_seconds,
    )
    return SessionResponse(
        configured=request.app.state.auth.configured(),
        authenticated=bool(identity),
        username=identity.username if identity else None,
        csrf_token=identity.csrf_token if identity else None,
    )


@router.post("/login", response_model=SessionResponse)
def login(payload: LoginRequest, request: Request, response: Response) -> SessionResponse:
    check_request_origin(request)
    if request.headers.get("x-open-node-client") != "browser":
        raise HTTPException(403, "Explicit login request header required")
    rate_limit(request)
    settings = request.app.state.settings
    result = request.app.state.auth.login(
        payload.username,
        payload.password.get_secret_value(),
        settings.session_lifetime_seconds,
    )
    if not result:
        raise HTTPException(
            401, "Invalid username or password", headers={"Cache-Control": "no-store"}
        )
    token, identity = result
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        request.app.state.auth.logout(previous)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_lifetime_seconds,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        configured=True,
        authenticated=True,
        username=identity.username,
        csrf_token=identity.csrf_token,
    )


@router.post("/logout", status_code=204, dependencies=[Depends(require_administrator)])
def logout(request: Request, response: Response) -> None:
    request.app.state.auth.logout(request.cookies[SESSION_COOKIE])
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=request.app.state.settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> None:
    rate_limit(request)
    if not request.app.state.auth.change_password(
        payload.current_password.get_secret_value(),
        payload.new_password.get_secret_value(),
    ):
        raise HTTPException(400, "Current password is incorrect")
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=request.app.state.settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
