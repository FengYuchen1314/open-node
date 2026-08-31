"""Explicit administrator creation and bounded encrypted-artifact downloads."""

import json
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError
from starlette.responses import StreamingResponse

from open_node.api.auth import SESSION_COOKIE
from open_node.api.backup import BackupAPIRoute
from open_node.domain.backup_jobs import BackupCreateRequest, BackupJobRead, BackupJobsRead
from open_node.services.backup_authorization import backup_session_hash
from open_node.services.backup_jobs import BackupJobError
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(route_class=BackupAPIRoute, prefix="/backups", tags=["backups"])
MAX_REQUEST_BYTES = 8192

BACKUP_ERROR_MESSAGES = {
    "backup_not_found": "备份任务不存在，或不属于当前登录会话。",
    "backup_busy": "已有备份任务或下载正在进行，请稍后重试。",
    "backup_not_ready": "备份尚未生成完成，暂不能下载。",
    "backup_request_conflict": "此请求编号已用于其他备份，请创建新请求。",
    "backup_worker_unavailable": "备份服务不可用，请确认使用默认的单 Web 进程部署。",
    "backup_authorization_expired": "备份授权已失效，或密码、验证码不正确，请重新验证。",
    "backup_creation_failed": "备份生成失败，请检查可用空间、密钥配置和数据目录权限。",
    "backup_expired": "此备份已过期，请重新创建。",
    "backup_invalid_request": "备份请求格式不正确，请检查公钥和输入内容。",
    "backup_rate_limited": "验证过于频繁，请一分钟后重试。",
}


def _manager(request: Request):
    manager = request.app.state.backup_jobs
    if manager is None or not manager.available:
        raise BackupJobError("backup_worker_unavailable", 503)
    return manager


def _owner(request: Request) -> str:
    return backup_session_hash(request.cookies.get(SESSION_COOKIE))


def _job_id(value: str) -> str:
    try:
        identifier = UUID(value)
        if identifier.version != 4 or str(identifier) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise BackupJobError("backup_not_found", 404) from None
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError()


async def _payload(request: Request) -> BackupCreateRequest:
    # The administrator dependency checks identity, Origin and CSRF first.
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "application/json"
    ):
        raise BackupJobError("backup_invalid_request", 415)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_REQUEST_BYTES:
            raise BackupJobError("backup_invalid_request", 413)
        content.extend(chunk)
    try:
        return BackupCreateRequest.model_validate(json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        ))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise BackupJobError("backup_invalid_request", 422) from None


@router.get("", response_model=BackupJobsRead)
def list_backups(request: Request):
    manager = request.app.state.backup_jobs
    available = manager is not None and manager.available
    return BackupJobsRead(
        available=available,
        unavailable_code=None if available else "backup_worker_unavailable",
        jobs=manager.list_jobs(_owner(request)) if available else [],
        requires_two_factor=request.app.state.auth.security().totp_enabled,
    )


def _create(request: Request, payload: BackupCreateRequest):
    manager = _manager(request)
    owner = _owner(request)
    # Serialize proof + reservation, so duplicate POSTs cannot consume a TOTP
    # twice or start a second job after the first response was lost.
    with request.app.state.backup_submission_lock:
        existing = manager.find_job(payload.request_id, owner, payload.recipient)
        if existing is not None:
            return existing
        authorization = request.app.state.backup_authorizer.issue(
            request.cookies[SESSION_COOKIE], payload.password.get_secret_value(),
            payload.code.get_secret_value(),
        )
        return manager.submit(payload.request_id, payload.recipient, authorization)


@router.post("", response_model=BackupJobRead, status_code=202)
async def create_backup(request: Request):
    payload = await _payload(request)
    return await run_in_backup_threadpool(_create, request, payload)


@router.get("/{job_id}", response_model=BackupJobRead)
def get_backup(job_id: str, request: Request):
    return _manager(request).get_job(_job_id(job_id), _owner(request))


@router.delete("/{job_id}", status_code=204)
def delete_backup(job_id: str, request: Request):
    _manager(request).delete_job(_job_id(job_id), _owner(request))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


class BackupDownloadResponse(Response):
    def __init__(self, manager, job_id: str, owner: str):
        super().__init__(content=b"")
        self.manager, self.job_id, self.owner = manager, job_id, owner

    async def __call__(self, scope, receive, send):
        # This context owns the reader through headers, every body chunk, and
        # disconnect cleanup. The synchronous read keeps its actual thread lease.
        with self.manager.download(self.job_id, self.owner) as download:
            async def chunks():
                while block := await run_in_backup_threadpool(download.read, 65536):
                    yield block

            response = StreamingResponse(
                chunks(), media_type="application/octet-stream", headers={
                    "Content-Disposition": f'attachment; filename="{download.filename}"',
                    "Content-Length": str(download.size),
                    "X-Content-SHA256": download.sha256,
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                    "Accept-Ranges": "none",
                },
            )
            await response(scope, receive, send)


@router.get("/{job_id}/download", response_class=Response)
def download_backup(job_id: str, request: Request):
    manager, owner = _manager(request), _owner(request)
    identifier = _job_id(job_id)
    job = manager.get_job(identifier, owner)
    if request.headers.get("range") is not None:
        raise BackupJobError("backup_invalid_request", 416)
    if job["status"] != "ready":
        raise BackupJobError("backup_not_ready", 409)
    return BackupDownloadResponse(manager, identifier, owner)
