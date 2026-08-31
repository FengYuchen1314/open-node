import asyncio
import contextlib
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from open_node.api.backup import BackupHTTPMiddleware
from open_node.api.router import api_router
from open_node.api.routes.agents import agent_websocket
from open_node.api.routes.backups import BACKUP_ERROR_MESSAGES
from open_node.api.routes.public import router as public_router
from open_node.api.routes.subscription_profiles import legacy_router
from open_node.api.routes.system import healthz
from open_node.api.routes.temporary_subscriptions import public_router as temporary_public_router
from open_node.core.config import Settings, get_settings
from open_node.domain.branding import BRANDING_ERROR_MESSAGES, BrandingError
from open_node.domain.inventory import AgentCommandPayloadError
from open_node.domain.notifications import NotificationError
from open_node.services.agent_bootstrap import AgentBootstrapStore
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.auth import AuthStore
from open_node.services.backup_authorization import BackupAuthorizationError, BackupAuthorizer
from open_node.services.backup_coordination import BackupWriteBarrier
from open_node.services.backup_jobs import BackupJobError, BackupJobManager
from open_node.services.backup_runtime import backup_operation, configured_backup_barrier
from open_node.services.backup_snapshot import BackupSnapshotError, configured_backup_layout
from open_node.services.branding import BrandingStore
from open_node.services.certificate_worker import CertificateWorker
from open_node.services.certificates import CertificateStore
from open_node.services.external_subscriptions import ExternalSubscriptionError
from open_node.services.inventory import InventoryStore, ManagedNodeConflict
from open_node.services.notification_worker import NotificationWorker
from open_node.services.notifications import NotificationStore
from open_node.services.probe_stream import PublicProbeStreamManager
from open_node.services.secure_channel import AgentIdentity, decode_public_key
from open_node.services.server_traffic import ServerTrafficWorker
from open_node.services.subscriber_auth import SubscriberAuthStore
from open_node.services.subscription_access import SubscriptionAccessWorker
from open_node.services.subscription_templates import (
    TemplateConflict,
    TemplateForbidden,
    TemplateNotFound,
)
from open_node.services.telegram_transport import TelegramTransport
from open_node.services.template_rendering import TemplateError
from open_node.web import FrontendFiles

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    backup_writes = configured_backup_barrier(active_settings.database_url)
    try:
        # Constructors and migrations can write before lifespan starts. They
        # participate in the same cross-process lock as HTTP and the admin CLI.
        with backup_operation(backup_writes):
            return _create_app(active_settings, backup_writes)
    except BaseException:
        backup_writes.close()
        raise


def _create_app(active_settings: Settings, backup_writes: BackupWriteBarrier) -> FastAPI:
    identity = (
        AgentIdentity.load(active_settings.agent_identity_file)
        if active_settings.agent_identity_file
        else None
    )

    @asynccontextmanager
    async def lifespan(app):
        with backup_operation(backup_writes):
            worker = CertificateWorker(
                app.state.certificates,
                app.state.agent_connections,
                backup_writes=backup_writes,
            )
            access = SubscriptionAccessWorker(
                app.state.inventory,
                app.state.agent_connections,
                active_settings.subscription_access_poll_seconds,
                backup_writes=backup_writes,
            )
            traffic = ServerTrafficWorker(
                app.state.inventory,
                active_settings.server_traffic_poll_seconds,
                backup_writes=backup_writes,
            )
            notification = NotificationWorker(
                app.state.notifications,
                app.state.notification_transport,
                interval=active_settings.notifications_poll_seconds,
                backup_writes=backup_writes,
            )
        # Each actual cycle establishes its own lease. Idle workers must not
        # inherit an initialization operation or prevent a snapshot forever.
        task = asyncio.create_task(worker.run())
        access_task = asyncio.create_task(access.run())
        traffic_task = asyncio.create_task(traffic.run())
        notification_task = asyncio.create_task(notification.run())
        try:
            if app.state.backup_jobs is not None:
                await asyncio.to_thread(app.state.backup_jobs.start)
            yield
        finally:
            task.cancel()
            access_task.cancel()
            traffic_task.cancel()
            notification_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(asyncio.CancelledError):
                await access_task
            with contextlib.suppress(asyncio.CancelledError):
                await traffic_task
            with contextlib.suppress(asyncio.CancelledError):
                await notification_task
            if app.state.backup_jobs is not None:
                # A timeout stops admission, not the actual producer thread.
                # It retains its barrier and closes private resources on exit.
                await asyncio.to_thread(app.state.backup_jobs.close)

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
                    active_settings.api_prefix + "/backups",
                    active_settings.api_prefix + "/agents/bootstrap/",
                    active_settings.api_prefix + "/external-subscriptions",
                    active_settings.api_prefix + "/notifications",
                    active_settings.api_prefix + "/system-settings/branding",
                    active_settings.api_prefix + "/branding",
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
        if request.url.path.startswith(active_settings.api_prefix + "/system-settings/branding"):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "branding_invalid_request",
                    "detail": BRANDING_ERROR_MESSAGES["branding_invalid_request"],
                    "license_required": False,
                },
            )
        if request.url.path.startswith(active_settings.api_prefix + "/notifications"):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "notification_invalid_request",
                    "detail": "Invalid notification request.",
                    "license_required": False,
                },
            )
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

    @app.exception_handler(ExternalSubscriptionError)
    async def invalid_external_subscription(_request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc), "license_required": False},
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.exception_handler(BrandingError)
    async def invalid_branding(_request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "detail": str(exc), "license_required": False},
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.exception_handler(NotificationError)
    async def invalid_notification(_request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "detail": str(exc), "license_required": False},
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.exception_handler(BackupJobError)
    @app.exception_handler(BackupAuthorizationError)
    async def invalid_backup(_request, exc):
        code = exc.code if exc.code in BACKUP_ERROR_MESSAGES else "backup_creation_failed"
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        if exc.status_code == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code, "detail": BACKUP_ERROR_MESSAGES[code], "license_required": False,
            },
            headers=headers,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        BackupHTTPMiddleware, barrier=backup_writes, api_prefix=active_settings.api_prefix
    )
    app.state.settings = active_settings
    # This barrier belongs to the app, not a single lifespan context. Actual
    # worker references retain it after cancellation; idle instances use the
    # barrier's finalizer instead of closing live or reusable app resources.
    app.state.backup_writes = backup_writes
    app.state.agent_identity = identity
    app.state.auth = AuthStore(
        active_settings.database_url,
        active_settings.subscriber_totp_key,
        active_settings.app_name,
    )
    app.state.backup_authorizer = BackupAuthorizer(
        app.state.auth, active_settings.session_idle_seconds,
    )
    app.state.backup_submission_lock = threading.Lock()
    try:
        backup_layout = configured_backup_layout(active_settings)
    except BackupSnapshotError:
        app.state.backup_jobs = None
    else:
        app.state.backup_jobs = BackupJobManager(
            backup_layout, backup_writes,
            is_authorized=app.state.backup_authorizer.is_authorized,
            totp_key=(active_settings.subscriber_totp_key.get_secret_value().encode()
                      if active_settings.subscriber_totp_key else None),
            agent_public_key=(decode_public_key(identity.public_metadata()["public_key"])
                              if identity else None),
            temporary_directory=active_settings.backup_temporary_directory,
        )
    app.state.inventory = InventoryStore(
        active_settings.database_url,
        short_links_enabled=active_settings.short_links_enabled,
        external_subscriptions_state_dir=active_settings.external_subscriptions_state_dir,
    )
    notification_state_dir = active_settings.notifications_state_dir
    database_file = app.state.inventory._engine.url.database
    if (
        notification_state_dir is None
        and app.state.inventory._engine.dialect.name == "sqlite"
        and database_file not in (None, "", ":memory:")
        and not database_file.startswith("file:")
    ):
        # Default Docker backups cover the SQLite database and its independent
        # notification key together. In-memory/URI databases never infer a path.
        notification_state_dir = Path(database_file).absolute().parent / "notifications"
    if notification_state_dir is not None:
        private_roots = [active_settings.certificate_state_dir.absolute()]
        if app.state.inventory.external_subscriptions_state_dir is not None:
            private_roots.append(app.state.inventory.external_subscriptions_state_dir.absolute())
        if any(
            notification_state_dir.is_relative_to(root)
            or root.is_relative_to(notification_state_dir)
            for root in private_roots
        ):
            raise ValueError("Notification secrets require a separate, non-overlapping directory")
    app.state.inventory.create_schema()
    app.state.branding = BrandingStore(app.state.inventory)
    try:
        app.state.branding.create_schema()
    except BrandingError:
        # Site labels must not make authentication or other services unavailable.
        # Reads still fail safely; the UI uses its built-in text fallback.
        log.warning("Branding settings could not be initialized")
    app.state.notifications = NotificationStore(app.state.inventory, notification_state_dir)
    app.state.notifications.create_schema()
    app.state.notification_transport = TelegramTransport()
    app.state.external_subscriptions = app.state.inventory.external_subscriptions()
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
