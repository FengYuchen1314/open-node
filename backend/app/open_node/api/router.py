from fastapi import APIRouter, Depends

from open_node.api.auth import require_administrator
from open_node.api.backup import BackupAPIRoute
from open_node.api.routes import (
    agent_bootstrap,
    agents,
    announcements,
    appearance,
    application_updates,
    auth,
    backups,
    branding,
    certificates,
    changes,
    ddns,
    external_subscriptions,
    initial_setup,
    legacy_mmwx,
    license,
    node_management,
    notifications,
    plan_management,
    private_routed_nodes,
    probe,
    public,
    registration_invitations,
    renewals,
    server_management,
    server_sharing,
    servers,
    subscriber_auth,
    subscriber_permissions,
    subscription_customizations,
    subscription_profiles,
    subscription_scripts,
    subscription_templates,
    subscriptions,
    system,
    temporary_subscriptions,
    user_management,
)

api_router = APIRouter(route_class=BackupAPIRoute)
private_router = APIRouter(
    route_class=BackupAPIRoute, dependencies=[Depends(require_administrator)]
)
private_router.include_router(servers.router)
private_router.include_router(announcements.router)
private_router.include_router(application_updates.router)
private_router.include_router(backups.router)
private_router.include_router(agent_bootstrap.router)
private_router.include_router(branding.router)
private_router.include_router(appearance.router)
private_router.include_router(server_management.router)
private_router.include_router(server_sharing.router)
private_router.include_router(server_sharing.consumer_router)
private_router.include_router(ddns.router)
private_router.include_router(certificates.router)
private_router.include_router(changes.router)
private_router.include_router(external_subscriptions.router)
private_router.include_router(legacy_mmwx.router)
private_router.include_router(probe.router)
private_router.include_router(subscriptions.router)
private_router.include_router(subscription_profiles.router)
private_router.include_router(subscription_customizations.router)
private_router.include_router(subscription_scripts.router)
private_router.include_router(temporary_subscriptions.router)
private_router.include_router(private_routed_nodes.router)
private_router.include_router(registration_invitations.router)
private_router.include_router(renewals.router)
private_router.include_router(plan_management.router)
private_router.include_router(user_management.router)
private_router.include_router(node_management.router)
private_router.include_router(notifications.router)
private_router.include_router(subscriber_auth.management_router)
private_router.include_router(subscriber_permissions.router)
api_router.include_router(private_router)
api_router.include_router(auth.router)
api_router.include_router(initial_setup.router)
api_router.include_router(subscriber_auth.router)
api_router.include_router(subscriber_permissions.account_router)
api_router.include_router(server_sharing.public_router)
api_router.include_router(announcements.account_router)
api_router.include_router(external_subscriptions.account_router)
api_router.include_router(subscription_customizations.account_router)
api_router.include_router(subscription_scripts.account_router)
api_router.include_router(renewals.account_router)
api_router.include_router(private_routed_nodes.account_router)
api_router.include_router(subscription_templates.router)
api_router.include_router(subscription_templates.router, prefix="/account")
api_router.include_router(system.router)
api_router.include_router(branding.public_router)
api_router.include_router(appearance.public_router)
api_router.include_router(license.router)
api_router.include_router(agents.router)
api_router.include_router(agent_bootstrap.public_router)
api_router.include_router(public.router)
api_router.include_router(subscriptions.public_router)
api_router.include_router(subscription_profiles.public_router)
