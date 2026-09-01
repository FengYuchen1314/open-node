"""Administrator and subscriber CRUD for isolated subscription scripts."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from open_node.api.backup import BackupAPIRoute
from open_node.api.routes.subscriber_auth import Identity
from open_node.api.routes.subscriber_permissions import require_feature
from open_node.domain.subscription_scripts import (
    AccountOverrideScriptCreate,
    OverrideScriptCreate,
    OverrideScriptDelete,
    OverrideScriptRead,
    OverrideScriptsResponse,
    OverrideScriptUpdate,
)
from open_node.services.subscription_scripts import SubscriptionScriptError

router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/subscription-scripts",
    tags=["subscription scripts"],
)
account_router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/account/subscription-scripts",
    tags=["subscriber subscription scripts"],
    dependencies=[Depends(require_feature("templates"))],
)


def _store(request):
    return request.app.state.inventory.subscription_scripts()


def _invoke(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except SubscriptionScriptError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("", response_model=OverrideScriptsResponse)
def list_scripts(request: Request):
    return OverrideScriptsResponse(scripts=_store(request).list())


@router.post("", response_model=OverrideScriptRead, status_code=201)
def create_script(payload: OverrideScriptCreate, request: Request):
    return _invoke(_store(request).create, payload)


@router.put("/{identifier}", response_model=OverrideScriptRead)
def update_script(identifier: UUID, payload: OverrideScriptUpdate, request: Request):
    return _invoke(_store(request).update, identifier, payload)


@router.post("/{identifier}/delete", status_code=204)
def delete_script(identifier: UUID, payload: OverrideScriptDelete, request: Request):
    _invoke(_store(request).delete, identifier, payload.expected_revision)


@account_router.get("", response_model=OverrideScriptsResponse)
def list_account_scripts(request: Request, identity: Identity):
    return OverrideScriptsResponse(
        scripts=_store(request).list(owner_username=identity.username)
    )


@account_router.post("", response_model=OverrideScriptRead, status_code=201)
def create_account_script(
    payload: AccountOverrideScriptCreate, request: Request, identity: Identity
):
    owned = OverrideScriptCreate(owner_username=identity.username, **payload.model_dump())
    return _invoke(
        _store(request).create, owned, owner_username=identity.username
    )


@account_router.put("/{identifier}", response_model=OverrideScriptRead)
def update_account_script(
    identifier: UUID, payload: OverrideScriptUpdate, request: Request, identity: Identity
):
    return _invoke(
        _store(request).update,
        identifier,
        payload,
        owner_username=identity.username,
    )


@account_router.post("/{identifier}/delete", status_code=204)
def delete_account_script(
    identifier: UUID, payload: OverrideScriptDelete, request: Request, identity: Identity
):
    _invoke(
        _store(request).delete,
        identifier,
        payload.expected_revision,
        owner_username=identity.username,
    )

