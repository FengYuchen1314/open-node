from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from open_node.api.router import api_router
from open_node.api.routes.agents import agent_websocket
from open_node.api.routes.public import router as public_router
from open_node.api.routes.system import healthz
from open_node.core.config import Settings, get_settings
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.auth import AuthStore
from open_node.services.inventory import InventoryStore
from open_node.services.probe_stream import PublicProbeStreamManager


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

    app = FastAPI(
        title=active_settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = active_settings
    app.state.auth = AuthStore(active_settings.database_url)
    app.state.inventory = InventoryStore(active_settings.database_url)
    app.state.inventory.create_schema()
    app.state.agent_connections = AgentConnectionManager()
    app.state.public_probe_streams = PublicProbeStreamManager()
    app.include_router(api_router, prefix=active_settings.api_prefix)
    app.add_api_websocket_route("/api/remote/ws", agent_websocket)
    app.include_router(public_router, prefix="/api")
    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)
    return app


app = create_app()
