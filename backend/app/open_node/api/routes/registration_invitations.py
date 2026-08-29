from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from open_node.api.dependencies import get_inventory_store
from open_node.domain.registration_invitations import (
    RegistrationInvitationCreate,
    RegistrationInvitationCreateResponse,
    RegistrationInvitationRead,
    RegistrationInvitationsResponse,
)
from open_node.services.inventory import InventoryStore, SubscriptionPlanNotFoundError
from open_node.services.registration_invitations import (
    RegistrationInvitationConflict,
    RegistrationInvitationUnavailable,
)

router = APIRouter(prefix="/registration-invitations", tags=["registration invitations"])


@router.get("", response_model=RegistrationInvitationsResponse)
def list_registration_invitations(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    return store._registration_invitations().list()


@router.post(
    "", response_model=RegistrationInvitationCreateResponse, status_code=status.HTTP_201_CREATED
)
def create_registration_invitation(
    payload: RegistrationInvitationCreate,
    request: Request,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    try:
        issued = store._registration_invitations().create(payload)
    except SubscriptionPlanNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    base = str(request.base_url).rstrip("/")
    return RegistrationInvitationCreateResponse(
        invitation=issued.invitation,
        registration_url=f"{base}/account#invite={quote(issued.token, safe='')}",
    )


@router.delete("/{identifier}", response_model=RegistrationInvitationRead)
def revoke_registration_invitation(
    identifier: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    try:
        return store._registration_invitations().revoke(identifier)
    except RegistrationInvitationUnavailable as exc:
        raise HTTPException(404, str(exc)) from exc
    except RegistrationInvitationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
