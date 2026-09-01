"""Administrator and subscriber APIs for subscription customizations."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from open_node.api.backup import BackupAPIRoute
from open_node.api.routes.subscriber_auth import Identity
from open_node.api.routes.subscriber_permissions import require_feature
from open_node.domain.subscription_customizations import (
    AccountCustomRuleCreate,
    AccountProxyProviderCreate,
    CustomizationDelete,
    CustomRuleCreate,
    CustomRuleRead,
    CustomRulesResponse,
    CustomRuleUpdate,
    ProxyProviderCreate,
    ProxyProviderRead,
    ProxyProvidersResponse,
    ProxyProviderUpdate,
)
from open_node.services.subscription_customizations import SubscriptionCustomizationError

router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/subscription-customizations",
    tags=["subscription customizations"],
)
account_router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/account/subscription-customizations",
    tags=["subscriber subscription customizations"],
)


def _store(request):
    return request.app.state.inventory.subscription_customizations()


def _invoke(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except SubscriptionCustomizationError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/rules", response_model=CustomRulesResponse)
def list_rules(request: Request):
    return CustomRulesResponse(rules=_store(request).list_rules())


@router.post("/rules", response_model=CustomRuleRead, status_code=201)
def create_rule(payload: CustomRuleCreate, request: Request):
    return _invoke(_store(request).create_rule, payload)


@router.put("/rules/{identifier}", response_model=CustomRuleRead)
def update_rule(identifier: UUID, payload: CustomRuleUpdate, request: Request):
    return _invoke(_store(request).update_rule, identifier, payload)


@router.post("/rules/{identifier}/delete", status_code=204)
def delete_rule(identifier: UUID, payload: CustomizationDelete, request: Request):
    _invoke(_store(request).delete_rule, identifier, payload.expected_revision)


@router.get("/providers", response_model=ProxyProvidersResponse)
def list_providers(request: Request):
    return ProxyProvidersResponse(providers=_store(request).list_providers())


@router.post("/providers", response_model=ProxyProviderRead, status_code=201)
def create_provider(payload: ProxyProviderCreate, request: Request):
    return _invoke(_store(request).create_provider, payload)


@router.put("/providers/{identifier}", response_model=ProxyProviderRead)
def update_provider(identifier: UUID, payload: ProxyProviderUpdate, request: Request):
    return _invoke(_store(request).update_provider, identifier, payload)


@router.post("/providers/{identifier}/delete", status_code=204)
def delete_provider(identifier: UUID, payload: CustomizationDelete, request: Request):
    _invoke(_store(request).delete_provider, identifier, payload.expected_revision)


@account_router.get(
    "/rules",
    response_model=CustomRulesResponse,
    dependencies=[Depends(require_feature("templates"))],
)
def list_account_rules(request: Request, identity: Identity):
    return CustomRulesResponse(
        rules=_store(request).list_rules(owner_username=identity.username)
    )


@account_router.post(
    "/rules",
    response_model=CustomRuleRead,
    status_code=201,
    dependencies=[Depends(require_feature("templates"))],
)
def create_account_rule(payload: AccountCustomRuleCreate, request: Request, identity: Identity):
    owned = CustomRuleCreate(owner_username=identity.username, **payload.model_dump())
    return _invoke(
        _store(request).create_rule, owned, owner_username=identity.username
    )


@account_router.put(
    "/rules/{identifier}",
    response_model=CustomRuleRead,
    dependencies=[Depends(require_feature("templates"))],
)
def update_account_rule(
    identifier: UUID, payload: CustomRuleUpdate, request: Request, identity: Identity
):
    return _invoke(
        _store(request).update_rule,
        identifier,
        payload,
        owner_username=identity.username,
    )


@account_router.post(
    "/rules/{identifier}/delete",
    status_code=204,
    dependencies=[Depends(require_feature("templates"))],
)
def delete_account_rule(
    identifier: UUID, payload: CustomizationDelete, request: Request, identity: Identity
):
    _invoke(
        _store(request).delete_rule,
        identifier,
        payload.expected_revision,
        owner_username=identity.username,
    )


@account_router.get(
    "/providers",
    response_model=ProxyProvidersResponse,
    dependencies=[Depends(require_feature("external_subscriptions"))],
)
def list_account_providers(request: Request, identity: Identity):
    return ProxyProvidersResponse(
        providers=_store(request).list_providers(owner_username=identity.username)
    )


@account_router.post(
    "/providers",
    response_model=ProxyProviderRead,
    status_code=201,
    dependencies=[Depends(require_feature("external_subscriptions"))],
)
def create_account_provider(
    payload: AccountProxyProviderCreate, request: Request, identity: Identity
):
    owned = ProxyProviderCreate(owner_username=identity.username, **payload.model_dump())
    return _invoke(
        _store(request).create_provider, owned, owner_username=identity.username
    )


@account_router.put(
    "/providers/{identifier}",
    response_model=ProxyProviderRead,
    dependencies=[Depends(require_feature("external_subscriptions"))],
)
def update_account_provider(
    identifier: UUID, payload: ProxyProviderUpdate, request: Request, identity: Identity
):
    return _invoke(
        _store(request).update_provider,
        identifier,
        payload,
        owner_username=identity.username,
    )


@account_router.post(
    "/providers/{identifier}/delete",
    status_code=204,
    dependencies=[Depends(require_feature("external_subscriptions"))],
)
def delete_account_provider(
    identifier: UUID, payload: CustomizationDelete, request: Request, identity: Identity
):
    _invoke(
        _store(request).delete_provider,
        identifier,
        payload.expected_revision,
        owner_username=identity.username,
    )
