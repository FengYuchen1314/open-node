from fastapi import APIRouter, Depends

from open_node.api.auth import require_administrator
from open_node.api.routes import (
    agents,
    auth,
    certificates,
    changes,
    legacy_mmwx,
    license,
    node_management,
    plan_management,
    probe,
    public,
    server_management,
    servers,
    subscriber_auth,
    subscription_profiles,
    subscription_templates,
    subscriptions,
    system,
    temporary_subscriptions,
    user_management,
)

api_router = APIRouter()
private_router = APIRouter(dependencies=[Depends(require_administrator)])
private_router.include_router(servers.router)
private_router.include_router(server_management.router)
private_router.include_router(certificates.router)
private_router.include_router(changes.router)
private_router.include_router(legacy_mmwx.router)
private_router.include_router(probe.router)
private_router.include_router(subscriptions.router)
private_router.include_router(subscription_profiles.router)
private_router.include_router(temporary_subscriptions.router)
private_router.include_router(plan_management.router)
private_router.include_router(user_management.router)
private_router.include_router(node_management.router)
private_router.include_router(subscriber_auth.management_router)
api_router.include_router(private_router)
api_router.include_router(auth.router)
api_router.include_router(subscriber_auth.router)
api_router.include_router(subscription_templates.router)
api_router.include_router(subscription_templates.router, prefix="/account")
api_router.include_router(system.router)
api_router.include_router(license.router)
api_router.include_router(agents.router)
api_router.include_router(public.router)
api_router.include_router(subscriptions.public_router)
