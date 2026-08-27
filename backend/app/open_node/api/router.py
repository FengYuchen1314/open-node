from fastapi import APIRouter

from open_node.api.routes import license, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(license.router)
