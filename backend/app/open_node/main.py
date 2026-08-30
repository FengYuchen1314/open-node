import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from open_node.api.router import api_router
from open_node.api.routes.agents import agent_websocket
from open_node.api.routes.public import router as public_router
from open_node.api.routes.subscription_profiles import legacy_router
from open_node.api.routes.system import healthz
from open_node.api.routes.temporary_subscriptions import public_router as temporary_public_router
from open_node.core.config import Settings, get_settings
from open_node.domain.inventory import AgentCommandPayloadError
from open_node.services.agent_bootstrap import AgentBootstrapStore
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.auth import AuthStore
from open_node.services.certificate_worker import CertificateWorker
from open_node.services.certificates import CertificateStore
from open_node.services.inventory import InventoryStore, ManagedNodeConflict
from open_node.services.probe_stream import PublicProbeStreamManager
from open_node.services.secure_channel import AgentIdentity
from open_node.services.server_traffic import ServerTrafficWorker
from open_node.services.subscriber_auth import SubscriberAuthStore
from open_node.services.subscription_access import SubscriptionAccessWorker
from open_node.services.subscription_templates import (
    TemplateConflict,
    TemplateForbidden,
    TemplateNotFound,
)
from open_node.services.template_rendering import TemplateError
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

    def subscriber_request(request):
        path = request.url.path
        return path == active_settings.api_prefix + "/subscriber-accounts" or path.startswith(
            active_settings.api_prefix + "/account/"
        )

    def secret_request(request):
        path = request.url.path
        return (
            subscriber_request(request)
            or path.startswith(
                (
                    active_settings.api_prefix + "/migrations/mmwx/",
                    active_settings.api_prefix + "/auth/",
                    active_settings.api_prefix + "/agents/bootstrap/",
                )
            )
            or (
                path.startswith(active_settings.api_prefix + "/servers/")
                and path.rstrip("/").endswith("/bootstrap")
            )
        )

    @app.middleware("http")
    async def private_subscriber_responses(request, call_next):
        response = await call_next(request)
        if secret_request(request):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if secret_request(request):
            return JSONResponse(
                status_code=422,
                content={
                    "detail": [
                        {key: error[key] for key in ("loc", "msg", "type")}
                        for error in exc.errors()
                    ]
                },
            )
        return await request_validation_exception_handler(request, exc)

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

    @app.exception_handler(TemplateError)
    async def invalid_template(_request, exc):
        code = (
            404
            if isinstance(exc, TemplateNotFound)
            else 403
            if isinstance(exc, TemplateForbidden)
            else 409
            if isinstance(exc, TemplateConflict)
            else 422
        )
        return JSONResponse(
            status_code=code,
            content={"detail": str(exc), "license_required": False},
            headers={"Cache-Control": "no-store"},
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
    app.state.auth = AuthStore(
        active_settings.database_url,
        active_settings.subscriber_totp_key,
        active_settings.app_name,
    )
    app.state.inventory = InventoryStore(
        active_settings.database_url,
        short_links_enabled=active_settings.short_links_enabled,
    )
    app.state.inventory.create_schema()
    app.state.agent_bootstrap = AgentBootstrapStore(app.state.inventory)
    app.state.subscriber_auth = SubscriberAuthStore(app.state.inventory, active_settings)
    app.state.certificates = CertificateStore(active_settings, app.state.inventory)
    app.state.agent_connections = AgentConnectionManager()
    app.state.public_probe_streams = PublicProbeStreamManager()
    app.include_router(api_router, prefix=active_settings.api_prefix)
    app.add_api_websocket_route("/api/remote/ws", agent_websocket)
    app.include_router(public_router, prefix="/api")
    app.include_router(legacy_router)
    app.include_router(temporary_public_router)
    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)
    if active_settings.frontend_dir:
        app.mount(
            "/",
            FrontendFiles(active_settings.frontend_dir, active_settings.api_prefix),
            name="frontend",
        )
    return app


app = create_app()
