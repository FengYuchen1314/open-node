from fastapi import APIRouter, Depends

from open_node.api.auth import require_administrator
from open_node.api.routes import (
    agents,
    auth,
    certificates,
    changes,
    license,
    probe,
    public,
    servers,
    subscriptions,
    system,
)

api_router = APIRouter()
private_router = APIRouter(dependencies=[Depends(require_administrator)])
private_router.include_router(servers.router)
private_router.include_router(certificates.router)
private_router.include_router(changes.router)
private_router.include_router(probe.router)
private_router.include_router(subscriptions.router)
api_router.include_router(private_router)
api_router.include_router(auth.router)
api_router.include_router(system.router)
api_router.include_router(license.router)
api_router.include_router(agents.router)
api_router.include_router(public.router)
api_router.include_router(subscriptions.public_router)
