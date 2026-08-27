from fastapi import APIRouter

from open_node.api.routes import agents, license, public, servers, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(license.router)
api_router.include_router(servers.router)
api_router.include_router(agents.router)
api_router.include_router(public.router)
