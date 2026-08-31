from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from open_node.api.backup import BackupAPIRoute
from open_node.domain.certificates import (
    CertificateAccountUpdate,
    CertificateCreate,
    CertificateDeployment,
    CertificateImport,
    CertificateJobRequest,
    CertificateRevoke,
    CertificateUpdate,
    DNSProviderInput,
)
from open_node.services.certificates import CertificateError


class CertificateRoute(BackupAPIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                response = await original(request)
            except RequestValidationError as exc:
                response = JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {key: error[key] for key in ("loc", "msg", "type")}
                            for error in exc.errors()
                        ]
                    },
                )
            except CertificateError as exc:
                response = JSONResponse(status_code=409, content={"detail": str(exc)})
            except InvalidToken:
                response = JSONResponse(
                    status_code=503,
                    content={"detail": "Certificate vault key is unavailable or incorrect"},
                )
            except (ValueError, OSError):
                response = JSONResponse(
                    status_code=422,
                    content={"detail": "Invalid certificate material or unavailable private state"},
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = APIRouter(prefix="/certificates", tags=["certificates"], route_class=CertificateRoute)


@router.get("/capabilities")
def capabilities(request: Request):
    return request.app.state.certificates.capabilities()


@router.get("/providers")
def providers(request: Request):
    return {"providers": request.app.state.certificates.providers()}


@router.post("/providers", status_code=201)
def create_provider(request: Request, payload: DNSProviderInput):
    return request.app.state.certificates.save_provider(payload)


@router.put("/providers/{identifier}")
def edit_provider(request: Request, identifier: UUID, payload: DNSProviderInput):
    return request.app.state.certificates.save_provider(payload, identifier)


@router.delete("/providers/{identifier}")
def delete_provider(request: Request, identifier: UUID):
    request.app.state.certificates.delete_provider(identifier)
    return {"success": True}


@router.get("")
def certificates(request: Request):
    return {"certificates": request.app.state.certificates.list(), "license_required": False}


@router.post("", status_code=201)
def create(request: Request, payload: CertificateCreate):
    return request.app.state.certificates.create(payload)


@router.post("/import", status_code=201)
def import_certificate(request: Request, payload: CertificateImport):
    return request.app.state.certificates.import_certificate(payload)


@router.get("/{identifier}")
def detail(request: Request, identifier: UUID):
    return request.app.state.certificates.detail(identifier)


@router.patch("/{identifier}")
def edit(request: Request, identifier: UUID, payload: CertificateUpdate):
    return request.app.state.certificates.edit(identifier, payload)


@router.delete("/{identifier}")
def delete(request: Request, identifier: UUID):
    request.app.state.certificates.delete(identifier)
    return {"success": True, "node_files_retained": True}


@router.get("/{identifier}/material")
def export(request: Request, identifier: UUID, include_private_key: bool = False):
    data = request.app.state.certificates.export(identifier)
    return (
        data
        if include_private_key
        else {key: value for key, value in data.items() if key != "key_pem"}
    )


@router.post("/{identifier}/issue", status_code=202)
def issue(request: Request, identifier: UUID, payload: CertificateJobRequest):
    return request.app.state.certificates.queue(identifier, "issue", payload.force)


@router.post("/{identifier}/renew", status_code=202)
def renew(request: Request, identifier: UUID, payload: CertificateJobRequest):
    return request.app.state.certificates.queue(identifier, "renew", payload.force)


@router.post("/{identifier}/versions/{version_id}/activate")
def activate(request: Request, identifier: UUID, version_id: UUID):
    return request.app.state.certificates.activate(identifier, version_id)


@router.post("/{identifier}/account", status_code=202)
def update_account(request: Request, identifier: UUID, payload: CertificateAccountUpdate):
    return request.app.state.certificates.queue_account(identifier, payload)


@router.post("/{identifier}/account/jobs/{job_id}/retry", status_code=202)
def retry_account(request: Request, identifier: UUID, job_id: UUID):
    return request.app.state.certificates.retry_account(identifier, job_id)


@router.post("/{identifier}/versions/{version_id}/revoke", status_code=202)
def revoke(request: Request, identifier: UUID, version_id: UUID, payload: CertificateRevoke):
    return request.app.state.certificates.queue_revocation(identifier, version_id, payload)


@router.post("/{identifier}/targets", status_code=201)
def target(request: Request, identifier: UUID, payload: CertificateDeployment):
    return request.app.state.certificates.save_target(identifier, payload)


@router.delete("/{identifier}/targets/{target_id}")
def delete_target(request: Request, identifier: UUID, target_id: UUID):
    request.app.state.certificates.delete_target(identifier, target_id)
    return {"success": True, "queued_commands_retained": True}


@router.post("/{identifier}/targets/{target_id}/deploy", status_code=201)
async def deploy(request: Request, identifier: UUID, target_id: UUID):
    command = request.app.state.certificates.deploy(identifier, target_id)
    command = await request.app.state.agent_connections.dispatch_command(
        request.app.state.inventory, command
    )
    return {"command": command}
