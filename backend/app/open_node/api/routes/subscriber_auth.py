import asyncio
from secrets import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from open_node.api.auth import check_request_origin
from open_node.api.routes.subscriptions import _subscription_token_response
from open_node.domain.registration_invitations import RegistrationClaim, RegistrationClaimResponse
from open_node.domain.subscriber_auth import (
    SubscriberAccountRead,
    SubscriberAccountUpdate,
    SubscriberCode,
    SubscriberDeviceRead,
    SubscriberEnrollment,
    SubscriberLogin,
    SubscriberPasswordChange,
    SubscriberProfile,
    SubscriberProof,
    SubscriberRecoveryCodes,
    SubscriberSecondFactor,
    SubscriberSecurityRead,
    SubscriberSessionRead,
    SubscriberShortCodeUpdate,
)
from open_node.domain.subscription_profiles import SubscriberSubscriptionProfilesResponse
from open_node.domain.subscriptions import (
    ProductUserSubscriptionTokenResponse,
    SubscriptionIpPolicyRead,
    SubscriptionIpPolicyUpdate,
)
from open_node.services.inventory import ProductUserConflict, ProductUserNotFoundError
from open_node.services.registration_invitations import (
    RegistrationInvitationConflict,
    RegistrationInvitationUnavailable,
)
from open_node.services.subscriber_auth import (
    SubscriberAuthenticationError,
    SubscriberFactorUnavailable,
    SubscriberIdentity,
    SubscriberSessionExpired,
)
from open_node.services.subscription_access import SubscriptionAccessConflict

COOKIE = "open_node_subscriber"
router = APIRouter(prefix="/account", tags=["subscriber account"])
management_router = APIRouter(prefix="/subscriber-accounts", tags=["subscriber administration"])


def invoke(call, *args, login=False, **kwargs):
    try:
        return call(*args, **kwargs)
    except SubscriberFactorUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except SubscriberAuthenticationError as exc:
        code = 401 if login or isinstance(exc, SubscriberSessionExpired) else 400
        raise HTTPException(code, "Invalid credentials" if login else str(exc)) from exc
    except ProductUserNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductUserConflict as exc:
        raise HTTPException(409, str(exc)) from exc


def require_subscriber(request: Request):
    identity = request.app.state.subscriber_auth.authenticate(request.cookies.get(COOKIE))
    if identity is None:
        raise HTTPException(401, "Subscriber sign-in required")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_request_origin(request)
        if not compare_digest(
            request.headers.get("x-csrf-token", "").encode(), identity.csrf_token.encode()
        ):
            raise HTTPException(403, "Invalid CSRF token")
    return identity


Identity = Annotated[SubscriberIdentity, Depends(require_subscriber)]


def limit(request, username=None):
    peer = request.client.host if request.client else "unknown"
    bucket = "login" if "/login" in request.url.path else "security"
    keys = [("subscriber:peer:" + peer, 60)]
    if username:
        keys.append(("subscriber:" + bucket + ":username:" + username.strip(), 10))
    for key, maximum in keys:
        if not request.app.state.auth.allow_login_attempt(key, max_attempts=maximum):
            raise HTTPException(
                429, "Too many attempts; try again later", headers={"Retry-After": "60"}
            )
    return peer


def login_request(request):
    check_request_origin(request)
    if request.headers.get("x-open-node-client") != "browser":
        raise HTTPException(403, "Browser client header required")


def issued_session(request, response, token, identity):
    request.app.state.subscriber_auth.logout(request.cookies.get(COOKIE))
    settings = request.app.state.settings
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_lifetime_seconds,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return SubscriberSessionRead(
        authenticated=True, username=identity.username, csrf_token=identity.csrf_token
    )


@router.get("/session", response_model=SubscriberSessionRead)
def session(request: Request):
    identity = request.app.state.subscriber_auth.authenticate(request.cookies.get(COOKIE))
    return (
        SubscriberSessionRead(
            authenticated=True, username=identity.username, csrf_token=identity.csrf_token
        )
        if identity
        else SubscriberSessionRead()
    )


@router.post("/register", response_model=RegistrationClaimResponse, status_code=201)
async def register(payload: RegistrationClaim, request: Request):
    login_request(request)
    limit(request, payload.username)
    try:
        result = await asyncio.to_thread(
            request.app.state.inventory._registration_invitations().claim, payload
        )
    except RegistrationInvitationUnavailable as exc:
        raise HTTPException(404, "Invitation unavailable") from exc
    except RegistrationInvitationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except SubscriptionAccessConflict as exc:
        raise HTTPException(409, "Invitation plan cannot provision subscriber access") from exc

    commands = []
    for command in result.commands:
        commands.append(
            await request.app.state.agent_connections.dispatch_command(
                request.app.state.inventory, command
            )
        )
    return result.model_copy(update={"commands": commands})


@router.post("/login", response_model=SubscriberSessionRead)
def login(payload: SubscriberLogin, request: Request, response: Response):
    login_request(request)
    peer = limit(request, payload.username)
    token, identity, challenge = invoke(
        request.app.state.subscriber_auth.login,
        payload.username,
        payload.password.get_secret_value(),
        peer,
        request.headers.get("user-agent", ""),
        login=True,
    )
    if challenge:
        return SubscriberSessionRead(requires_2fa=True, challenge=challenge)
    return issued_session(request, response, token, identity)


@router.post("/login/verify", response_model=SubscriberSessionRead)
def verify_login(payload: SubscriberSecondFactor, request: Request, response: Response):
    login_request(request)
    peer = limit(request)
    token, identity = invoke(
        request.app.state.subscriber_auth.complete_login,
        payload.challenge.get_secret_value(),
        payload.code.get_secret_value(),
        peer,
        request.headers.get("user-agent", ""),
        login=True,
    )
    return issued_session(request, response, token, identity)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, identity: Identity):
    request.app.state.subscriber_auth.logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")


@router.get("/me", response_model=SubscriberProfile)
def profile(request: Request, identity: Identity):
    return invoke(request.app.state.subscriber_auth.profile, identity)


@router.post("/password", status_code=204)
def change_password(
    payload: SubscriberPasswordChange, request: Request, response: Response, identity: Identity
):
    limit(request, identity.username)
    invoke(request.app.state.subscriber_auth.change_password, identity, payload)
    response.delete_cookie(COOKIE, path="/")


@router.post("/subscription-token", response_model=ProductUserSubscriptionTokenResponse)
def subscription_token(request: Request, identity: Identity):
    token = invoke(request.app.state.subscriber_auth.subscription_token, identity)
    return _subscription_token_response(request, token)


@router.get("/subscription-profiles", response_model=SubscriberSubscriptionProfilesResponse)
def subscription_profiles(request: Request, identity: Identity):
    if not request.app.state.settings.short_links_enabled:
        return SubscriberSubscriptionProfilesResponse(profiles=[])
    profiles = request.app.state.inventory._subscription_profiles().subscriber_profiles(
        identity.username, request.url_for
    )
    return SubscriberSubscriptionProfilesResponse(profiles=profiles)


@router.get("/subscription-ip-policy", response_model=SubscriptionIpPolicyRead)
def subscription_ip_policy(request: Request, identity: Identity):
    return invoke(request.app.state.inventory._subscription_ip_policy().read, identity.username)


@router.put("/subscription-ip-policy", response_model=SubscriptionIpPolicyRead)
def update_subscription_ip_policy(
    payload: SubscriptionIpPolicyUpdate, request: Request, identity: Identity
):
    return invoke(
        request.app.state.inventory._subscription_ip_policy().update,
        identity.username,
        payload,
    )


@router.post("/subscription-token/reset", response_model=ProductUserSubscriptionTokenResponse)
def reset_subscription_token(payload: SubscriberProof, request: Request, identity: Identity):
    limit(request, identity.username)
    token = invoke(request.app.state.subscriber_auth.subscription_token, identity, payload)
    return _subscription_token_response(request, token)


@router.get("/sessions", response_model=list[SubscriberDeviceRead])
def sessions(request: Request, identity: Identity):
    return invoke(request.app.state.subscriber_auth.devices, identity)


@router.put("/subscription-short-code", response_model=ProductUserSubscriptionTokenResponse)
def update_subscription_short_code(
    payload: SubscriberShortCodeUpdate, request: Request, identity: Identity
):
    if not request.app.state.settings.short_links_enabled:
        raise HTTPException(403, "Short subscription links are disabled")
    limit(request, identity.username)
    token = invoke(request.app.state.subscriber_auth.set_short_code, identity, payload)
    return _subscription_token_response(request, token)


@router.delete("/sessions", status_code=204)
def revoke_other_sessions(request: Request, identity: Identity):
    invoke(request.app.state.subscriber_auth.revoke_device, identity)


@router.delete("/sessions/{identifier}", status_code=204)
def revoke_session(identifier: UUID, request: Request, response: Response, identity: Identity):
    invoke(request.app.state.subscriber_auth.revoke_device, identity, str(identifier))
    if str(identifier) == identity.session_id:
        response.delete_cookie(COOKIE, path="/")


@router.get("/security", response_model=SubscriberSecurityRead)
def security(request: Request, identity: Identity):
    return invoke(request.app.state.subscriber_auth.security, identity)


@router.post("/totp/setup", response_model=SubscriberEnrollment)
def setup_totp(payload: SubscriberProof, request: Request, identity: Identity):
    limit(request, identity.username)
    return invoke(request.app.state.subscriber_auth.begin_totp, identity, payload)


@router.post("/totp/confirm", response_model=SubscriberRecoveryCodes)
def confirm_totp(payload: SubscriberCode, request: Request, identity: Identity):
    limit(request, identity.username)
    codes = invoke(
        request.app.state.subscriber_auth.confirm_totp, identity, payload.code.get_secret_value()
    )
    return SubscriberRecoveryCodes(recovery_codes=codes)


@router.post("/totp/disable", status_code=204)
def disable_totp(payload: SubscriberProof, request: Request, identity: Identity):
    limit(request, identity.username)
    invoke(request.app.state.subscriber_auth.update_totp, identity, payload, disable=True)


@router.post("/totp/recovery-codes", response_model=SubscriberRecoveryCodes)
def recovery_codes(payload: SubscriberProof, request: Request, identity: Identity):
    limit(request, identity.username)
    codes = invoke(request.app.state.subscriber_auth.update_totp, identity, payload)
    return SubscriberRecoveryCodes(recovery_codes=codes)


@management_router.get("", response_model=SubscriberAccountRead)
def account_settings(
    request: Request, username: Annotated[str, Query(min_length=1, max_length=80)]
):
    return invoke(request.app.state.subscriber_auth.management, username)


@management_router.put("", response_model=SubscriberAccountRead)
def set_account_password(
    payload: SubscriberAccountUpdate,
    request: Request,
    username: Annotated[str, Query(min_length=1, max_length=80)],
):
    return invoke(request.app.state.subscriber_auth.set_password, username, payload)
