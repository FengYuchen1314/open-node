import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from open_node.api.router import api_router
from open_node.api.routes.agents import agent_websocket
from open_node.api.routes.public import router as public_router
from open_node.api.routes.system import healthz
from open_node.core.config import Settings, get_settings
from open_node.domain.inventory import AgentCommandPayloadError
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.auth import AuthStore
from open_node.services.certificate_worker import CertificateWorker
from open_node.services.certificates import CertificateStore
from open_node.services.inventory import InventoryStore, ManagedNodeConflict
from open_node.services.probe_stream import PublicProbeStreamManager
from open_node.services.secure_channel import AgentIdentity
from open_node.services.server_traffic import ServerTrafficWorker
from open_node.services.subscription_access import SubscriptionAccessWorker
from open_node.web import FrontendFiles


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    identity = (
        AgentIdentity.load(active_settings.agent_identity_file)
        if active_settings.agent_identity_file
        else None
    )

    @asynccontextmanager
    async def lifespan(app):
        worker = CertificateWorker(app.state.certificates, app.state.agent_connections)
        task = asyncio.create_task(worker.run())
        access = SubscriptionAccessWorker(
            app.state.inventory,
            app.state.agent_connections,
            active_settings.subscription_access_poll_seconds,
        )
        access_task = asyncio.create_task(access.run())
        traffic_task = asyncio.create_task(
            ServerTrafficWorker(
                app.state.inventory, active_settings.server_traffic_poll_seconds
            ).run()
        )
        try:
            yield
        finally:
            task.cancel()
            access_task.cancel()
            traffic_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(asyncio.CancelledError):
                await access_task
            with contextlib.suppress(asyncio.CancelledError):
                await traffic_task

    app = FastAPI(
        title=active_settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    @app.exception_handler(AgentCommandPayloadError)
    async def invalid_agent_command(_request, exc):
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "license_required": False},
        )

    @app.exception_handler(ManagedNodeConflict)
    async def conflicting_node_mutation(_request, exc):
        return JSONResponse(
            status_code=409, content={"detail": str(exc), "license_required": False}
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = active_settings
    app.state.agent_identity = identity
    app.state.auth = AuthStore(active_settings.database_url)
    app.state.inventory = InventoryStore(active_settings.database_url)
    app.state.inventory.create_schema()
    app.state.certificates = CertificateStore(active_settings, app.state.inventory)
    app.state.agent_connections = AgentConnectionManager()
    app.state.public_probe_streams = PublicProbeStreamManager()
    app.include_router(api_router, prefix=active_settings.api_prefix)
    app.add_api_websocket_route("/api/remote/ws", agent_websocket)
    app.include_router(public_router, prefix="/api")
    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)
    if active_settings.frontend_dir:
        app.mount(
            "/",
            FrontendFiles(active_settings.frontend_dir, active_settings.api_prefix),
            name="frontend",
        )
    return app


app = create_app()
