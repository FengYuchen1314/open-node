from fastapi import APIRouter

from open_node.api.routes import (
    agents,
    changes,
    license,
    probe,
    public,
    servers,
    subscriptions,
    system,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(license.router)
api_router.include_router(servers.router)
api_router.include_router(agents.router)
api_router.include_router(changes.router)
api_router.include_router(probe.router)
api_router.include_router(public.router)
api_router.include_router(subscriptions.router)
