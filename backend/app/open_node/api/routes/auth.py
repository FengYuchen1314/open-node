from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from open_node.api.auth import SESSION_COOKIE, check_request_origin, require_administrator
from open_node.api.backup import BackupAPIRoute
from open_node.domain.auth import (
    AdministratorCode,
    AdministratorPolicyUpdate,
    AdministratorProfileRead,
    AdministratorProfileUpdate,
    AdministratorProof,
    AdministratorRecoveryCodes,
    AdministratorSecurityRead,
    AdministratorTotpEnrollment,
    LoginRequest,
    LoginResponse,
    LoginSecondFactorRequest,
    PasswordChangeRequest,
    SessionResponse,
)
from open_node.services.auth import (
    AdministratorAuthenticationError,
    AdministratorFactorUnavailable,
    AdministratorRateLimited,
    AdministratorSecurityConflict,
    AuthenticationResult,
    SessionIdentity,
)

router = APIRouter(route_class=BackupAPIRoute, prefix="/auth", tags=["authentication"])


def rate_limit(request: Request) -> None:
    peer = request.client.host if request.client else "unknown"
    if not request.app.state.auth.allow_login_attempt(peer):
        raise HTTPException(
            429, "Too many attempts; try again shortly", headers={"Retry-After": "60"}
        )


def login_request(request: Request) -> None:
    check_request_origin(request)
    if request.headers.get("x-open-node-client") != "browser":
        raise HTTPException(403, "Explicit login request header required")
    rate_limit(request)


def invoke(function, *args, login: bool = False, **kwargs):
    try:
        return function(*args, **kwargs)
    except AdministratorAuthenticationError as exc:
        raise HTTPException(401 if login else 400, str(exc)) from exc
    except AdministratorSecurityConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except AdministratorFactorUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except AdministratorRateLimited as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc


def set_session_cookie(request: Request, response: Response, result: AuthenticationResult) -> None:
    if not result.token or not result.identity:
        raise HTTPException(500, "Authentication session was not issued")
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        request.app.state.auth.logout(previous)
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        result.token,
        max_age=settings.session_lifetime_seconds,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )


def login_response(result: AuthenticationResult) -> LoginResponse:
    enrollment = (
        AdministratorTotpEnrollment(
            secret=result.enrollment.secret,
            provisioning_uri=result.enrollment.provisioning_uri,
            expires_at=result.enrollment.expires_at,
        )
        if result.enrollment
        else None
    )
    return LoginResponse(
        configured=True,
        authenticated=bool(result.token and result.identity),
        username=result.identity.username if result.identity else None,
        csrf_token=result.identity.csrf_token if result.identity else None,
        requires_2fa=bool(result.challenge),
        challenge=result.challenge,
        enrollment_required=bool(result.enrollment),
        enrollment=enrollment,
        recovery_codes=list(result.recovery_codes),
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


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response) -> LoginResponse:
    try:
        login_request(request)
    except HTTPException as exc:
        if exc.status_code == 429:
            request.app.state.security.record_login_failure(
                request.client.host if request.client else "", payload.username, locked=True,
            )
        raise
    settings = request.app.state.settings
    result = invoke(
        request.app.state.auth.login,
        payload.username,
        payload.password.get_secret_value(),
        settings.session_lifetime_seconds,
        login=True,
    )
    if not result:
        request.app.state.security.record_login_failure(
            request.client.host if request.client else "", payload.username,
        )
        raise HTTPException(
            401, "Invalid username or password", headers={"Cache-Control": "no-store"}
        )
    if result.token:
        set_session_cookie(request, response, result)
    response.headers["Cache-Control"] = "no-store"
    return login_response(result)


@router.post("/login/verify", response_model=LoginResponse)
def verify_login(
    payload: LoginSecondFactorRequest, request: Request, response: Response
) -> LoginResponse:
    login_request(request)
    result = invoke(
        request.app.state.auth.complete_login,
        payload.challenge.get_secret_value(),
        payload.code.get_secret_value(),
        request.app.state.settings.session_lifetime_seconds,
        login=True,
    )
    set_session_cookie(request, response, result)
    response.headers["Cache-Control"] = "no-store"
    return login_response(result)


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


@router.get("/security", response_model=AdministratorSecurityRead)
def security(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> AdministratorSecurityRead:
    del identity
    return AdministratorSecurityRead(**request.app.state.auth.security().__dict__)


@router.post("/totp/setup", response_model=AdministratorTotpEnrollment)
def setup_totp(
    payload: AdministratorProof,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> AdministratorTotpEnrollment:
    del identity
    rate_limit(request)
    result = invoke(
        request.app.state.auth.begin_totp,
        request.cookies[SESSION_COOKIE],
        payload.password.get_secret_value(),
    )
    return AdministratorTotpEnrollment(**result.__dict__)


@router.post("/totp/confirm", response_model=AdministratorRecoveryCodes)
def confirm_totp(
    payload: AdministratorCode,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> AdministratorRecoveryCodes:
    del identity
    rate_limit(request)
    codes = invoke(
        request.app.state.auth.confirm_totp,
        request.cookies[SESSION_COOKIE],
        payload.code.get_secret_value(),
    )
    return AdministratorRecoveryCodes(recovery_codes=list(codes))


@router.post("/totp/disable", status_code=204)
def disable_totp(
    payload: AdministratorProof,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> None:
    del identity
    rate_limit(request)
    invoke(
        request.app.state.auth.update_totp,
        request.cookies[SESSION_COOKIE],
        payload.password.get_secret_value(),
        payload.code.get_secret_value(),
        disable=True,
    )


@router.post("/totp/recovery-codes", response_model=AdministratorRecoveryCodes)
def recovery_codes(
    payload: AdministratorProof,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> AdministratorRecoveryCodes:
    del identity
    rate_limit(request)
    codes = invoke(
        request.app.state.auth.update_totp,
        request.cookies[SESSION_COOKIE],
        payload.password.get_secret_value(),
        payload.code.get_secret_value(),
    )
    return AdministratorRecoveryCodes(recovery_codes=list(codes))


@router.put("/security/policy", response_model=AdministratorSecurityRead)
def update_security_policy(
    payload: AdministratorPolicyUpdate,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> AdministratorSecurityRead:
    del identity
    rate_limit(request)
    result = invoke(
        request.app.state.auth.update_policy,
        request.cookies[SESSION_COOKIE],
        payload.password.get_secret_value(),
        payload.code.get_secret_value(),
        payload.required,
    )
    return AdministratorSecurityRead(**result.__dict__)


@router.get("/profile", response_model=AdministratorProfileRead)
def administrator_profile(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> AdministratorProfileRead:
    return AdministratorProfileRead(**request.app.state.auth.profile(identity.username).__dict__)


@router.put("/profile", response_model=AdministratorProfileRead)
def update_administrator_profile(
    payload: AdministratorProfileUpdate,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
) -> AdministratorProfileRead:
    result = invoke(
        request.app.state.auth.update_profile,
        identity.username,
        email=payload.email,
        nickname=payload.nickname,
        avatar_url=payload.avatar_url,
        expected_revision=payload.expected_revision,
    )
    return AdministratorProfileRead(**result.__dict__)
