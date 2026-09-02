import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.concurrency import run_in_threadpool

from open_node.api.auth import check_request_origin
from open_node.api.backup import BackupAPIRoute
from open_node.domain.agent_bootstrap import AgentBootstrapTransport
from open_node.services.agent_bootstrap import (
    AgentBootstrapRedemptionError,
    AgentBootstrapUnavailableError,
)
from open_node.services.agent_bootstrap_release import (
    AgentBootstrapArtifactUnavailable,
    AgentBootstrapReleaseUnavailable,
    installation_command,
    installer_bytes,
    release_manifest,
)
from open_node.services.backup_runtime import run_in_backup_threadpool
from open_node.services.inventory import ServerNotFoundError

router = APIRouter(route_class=BackupAPIRoute, prefix="/servers", tags=["agent bootstrap"])
public_router = APIRouter(
    route_class=BackupAPIRoute, prefix="/agents/bootstrap", tags=["agent bootstrap"]
)


class BootstrapIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transport: AgentBootstrapTransport = "auto"


class BootstrapRedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    ticket: SecretStr = Field(min_length=43, max_length=43, repr=False)
    claim_nonce: SecretStr = Field(min_length=43, max_length=43, repr=False)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate request field")
        result[key] = value
    return result


def invoke(function, *args):
    try:
        return function(*args)
    except ServerNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except AgentBootstrapUnavailableError as exc:
        raise HTTPException(409, str(exc)) from None
    except AgentBootstrapRedemptionError:
        raise HTTPException(401, "Invalid or expired installation ticket") from None
    except AgentBootstrapReleaseUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except AgentBootstrapArtifactUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


def availability(request: Request) -> dict:
    control_url = request.app.state.settings.agent_bootstrap_public_url
    try:
        manifest = release_manifest()
        installer_bytes()
    except AgentBootstrapReleaseUnavailable:
        return {
            "configured": False,
            "control_url": control_url,
            "release": None,
            "reason": "Verified Agent release is not available",
        }
    configured = bool(control_url) and request.app.state.settings.api_prefix == "/api/v1"
    return {
        "configured": configured,
        "control_url": control_url,
        "release": {
            "agent_version": manifest["agent"]["version"],
            "source_commit": manifest["agent"]["source_commit"],
            "xray_version": manifest["xray"]["version"],
            "mihomo_version": manifest["mihomo"]["version"],
            "platform": "Debian 12/13、Ubuntu 24.04/26.04 amd64 / systemd",
        },
        "reason": (
            None
            if configured
            else "Set OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL to the canonical HTTPS control-plane "
            "URL; the Agent requires the default /api/v1 prefix"
        ),
    }


@router.get("/{server_id}/bootstrap")
def bootstrap_status(server_id: UUID, request: Request) -> dict:
    state = invoke(request.app.state.agent_bootstrap.read, server_id)
    return {
        "bootstrap": state.model_dump(mode="json"),
        **availability(request),
        "license_required": False,
    }


@router.post("/{server_id}/bootstrap", status_code=201)
def issue_bootstrap(server_id: UUID, payload: BootstrapIssueRequest, request: Request) -> dict:
    available = availability(request)
    if not available["configured"]:
        raise HTTPException(503, available["reason"])
    issued = invoke(
        request.app.state.agent_bootstrap.issue,
        server_id,
        available["control_url"],
        payload.transport,
    )
    ticket = issued.ticket.get_secret_value()
    command = invoke(installation_command, issued.control_url, ticket, issued.server_id)
    return {
        "issued": issued.model_dump(mode="json", exclude={"ticket"}),
        "command": command,
        "license_required": False,
    }


@router.delete("/{server_id}/bootstrap")
def revoke_bootstrap(server_id: UUID, request: Request) -> dict:
    state = invoke(request.app.state.agent_bootstrap.revoke, server_id)
    return {
        "bootstrap": state.model_dump(mode="json"),
        **availability(request),
        "license_required": False,
    }


@public_router.get("/manifest")
def bootstrap_manifest() -> dict:
    return invoke(release_manifest)


@public_router.get("/installer.py", response_class=Response)
def bootstrap_installer() -> Response:
    return Response(
        invoke(installer_bytes),
        media_type="text/x-python; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="open-node-agent-install.py"',
        },
    )


@public_router.get("/artifacts/{filename}", response_class=FileResponse)
async def bootstrap_artifact(filename: str, request: Request) -> FileResponse:
    try:
        path, artifact = await run_in_threadpool(
            request.app.state.agent_bootstrap_artifacts.get, filename
        )
    except AgentBootstrapArtifactUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=artifact.filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": artifact.sha256,
            "Content-Length": str(artifact.size),
        },
    )


@public_router.post("/redeem")
async def redeem_bootstrap(request: Request) -> dict:
    check_request_origin(request)
    peer = request.client.host if request.client else "unknown"
    allowed = await run_in_backup_threadpool(
        request.app.state.auth.allow_login_attempt, "agent-bootstrap:" + peer
    )
    if not allowed:
        raise HTTPException(
            429, "Too many attempts; try again shortly", headers={"Retry-After": "60"}
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(415, "Use an application/json request body")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 8192:
            raise HTTPException(413, "Installation request is too large")
        body.extend(chunk)
    try:
        payload = BootstrapRedeemRequest.model_validate(
            json.loads(body, object_pairs_hook=_unique_fields)
        )
    except (ValueError, RecursionError):
        raise HTTPException(401, "Invalid or expired installation ticket") from None
    configuration = await run_in_backup_threadpool(
        invoke, request.app.state.agent_bootstrap.redeem, payload.ticket, payload.claim_nonce
    )
    result = configuration.model_dump(mode="json", exclude={"agent_token"})
    result["agent_token"] = configuration.agent_token.get_secret_value()
    return {"configuration": result, "license_required": False}
