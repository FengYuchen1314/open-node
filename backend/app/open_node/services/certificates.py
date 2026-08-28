import os
from ipaddress import ip_address
from time import time
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    String,
    Text,
    UniqueConstraint,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from open_node.domain.certificates import DNS_FIELDS, DNS_REQUIRED
from open_node.domain.inventory import AgentCommandCreate
from open_node.services.certificate_vault import CertificateVault, covers, material
from open_node.services.inventory import (
    AgentScanResultModel,
    CommandModel,
    ServerModel,
    create_inventory_engine,
)


class CertificateError(ValueError):
    pass


class CertificateBase(DeclarativeBase):
    pass


class DNSProvider(CertificateBase):
    __tablename__ = "certificate_dns_providers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(32))
    credentials: Mapped[str] = mapped_column(Text)
    credential_fields: Mapped[list] = mapped_column(JSON)


class ManagedCertificate(CertificateBase):
    __tablename__ = "managed_certificates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    domains: Mapped[list] = mapped_column(JSON)
    email: Mapped[str | None] = mapped_column(String(320))
    provider_id: Mapped[str | None] = mapped_column(String(36))
    challenge_type: Mapped[str] = mapped_column(String(16), default="dns", server_default="dns")
    webroot_id: Mapped[str | None] = mapped_column(String(64))
    directory_url: Mapped[str | None] = mapped_column(String(1024))
    eab: Mapped[str | None] = mapped_column(Text)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="idle")
    active_job_id: Mapped[str | None] = mapped_column(String(36))
    version_id: Mapped[str | None] = mapped_column(String(36))
    not_before: Mapped[float | None] = mapped_column(Float)
    expires_at: Mapped[float | None] = mapped_column(Float)
    next_attempt: Mapped[float] = mapped_column(Float, default=0)
    last_error: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[float] = mapped_column(Float, default=time)


class CertificateJob(CertificateBase):
    __tablename__ = "certificate_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    certificate_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    force: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    created_at: Mapped[float] = mapped_column(Float, default=time)
    finished_at: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(String(512))


class CertificateVersion(CertificateBase):
    __tablename__ = "certificate_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    certificate_id: Mapped[str] = mapped_column(String(36), index=True)
    encrypted_material: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time)


class CertificateTarget(CertificateBase):
    __tablename__ = "certificate_targets"
    __table_args__ = (UniqueConstraint("server_id", "cert_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    certificate_id: Mapped[str] = mapped_column(String(36), index=True)
    server_id: Mapped[str] = mapped_column(String(36))
    domain: Mapped[str] = mapped_column(String(255))
    cert_name: Mapped[str] = mapped_column(String(255))
    reload: Mapped[str] = mapped_column(String(16))
    auto_deploy: Mapped[bool] = mapped_column(Boolean)
    version_id: Mapped[str | None] = mapped_column(String(36))
    command_id: Mapped[str | None] = mapped_column(String(36))
    last_error: Mapped[str | None] = mapped_column(String(512))


def public_row(row, exclude=()):
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in exclude
    }


class CertificateStore:
    def __init__(self, settings, inventory):
        self.settings, self.inventory = settings, inventory
        self.engine = create_inventory_engine(settings.database_url)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
        CertificateBase.metadata.create_all(self.engine)
        self._migrate_schema()
        with self.session() as db:
            initialized = (
                db.scalar(select(DNSProvider.id).limit(1)) is not None
                or db.scalar(select(CertificateVersion.id).limit(1)) is not None
                or db.scalar(
                    select(ManagedCertificate.id)
                    .where(ManagedCertificate.eab.is_not(None))
                    .limit(1)
                )
                is not None
            )
        self.vault = CertificateVault(settings.certificate_state_dir, initialized=initialized)

    def _migrate_schema(self):
        if self.engine.dialect.name != "sqlite":
            return
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            columns = {
                column["name"] for column in inspect(connection).get_columns("managed_certificates")
            }
            for name, kind in {
                "challenge_type": "VARCHAR(16) NOT NULL DEFAULT 'dns'",
                "webroot_id": "VARCHAR(64)",
            }.items():
                if name not in columns:
                    connection.execute(
                        text(f"ALTER TABLE managed_certificates ADD COLUMN {name} {kind}")
                    )

    def capabilities(self):
        binary = self.settings.certificate_lego_binary
        return {
            "available": bool(binary and binary.is_file() and os.access(binary, os.X_OK)),
            "license_required": False,
            "directories": self.settings.certificate_acme_directories,
            "challenge_types": [
                "dns",
                *(["standalone"] if self.settings.certificate_http_address else []),
                *(["webroot"] if self.settings.certificate_webroots else []),
            ],
            "webroots": sorted(self.settings.certificate_webroots),
            "providers": [
                {"id": name, "fields": fields, "required": DNS_REQUIRED[name]}
                for name, fields in DNS_FIELDS.items()
            ],
        }

    @staticmethod
    def get(db, model, identifier):
        row = db.get(model, str(identifier))
        if row is None:
            raise CertificateError("Certificate resource not found")
        return row

    def providers(self):
        with self.session() as db:
            return [
                public_row(row, {"credentials"})
                for row in db.scalars(select(DNSProvider).order_by(DNSProvider.name))
            ]

    def save_provider(self, payload, identifier=None):
        credentials = {key: value.get_secret_value() for key, value in payload.credentials.items()}
        allowed = set(DNS_FIELDS[payload.provider])
        if not set(credentials) <= allowed or any(
            not credentials.get(key) for key in DNS_REQUIRED[payload.provider]
        ):
            raise CertificateError("Missing required or unsupported DNS credential fields")
        if any(len(value) > 8192 or "\0" in value for value in credentials.values()):
            raise CertificateError("Invalid DNS credential value")
        if payload.provider == "httpreq":
            endpoint = urlsplit(credentials["HTTPREQ_ENDPOINT"])
            try:
                loopback = ip_address(endpoint.hostname or "").is_loopback
            except ValueError:
                loopback = False
            if (
                (
                    endpoint.scheme != "https"
                    and not (
                        endpoint.scheme == "http"
                        and loopback
                        and self.settings.certificate_allow_loopback_http
                    )
                )
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.fragment
            ):
                raise CertificateError("DNS webhook requires HTTPS without URL credentials")
        encrypted = self.vault.seal(credentials)
        with self.session.begin() as db:
            row = (
                self.get(db, DNSProvider, identifier)
                if identifier
                else DNSProvider(id=str(uuid4()))
            )
            if identifier and row.provider != payload.provider:
                raise CertificateError("A DNS provider's type cannot be changed")
            row.name, row.provider = payload.name, payload.provider
            row.credentials, row.credential_fields = encrypted, sorted(credentials)
            db.add(row)
            return public_row(row, {"credentials"})

    def delete_provider(self, identifier):
        with self.session.begin() as db:
            row = self.get(db, DNSProvider, identifier)
            if db.scalar(
                select(ManagedCertificate.id)
                .where(ManagedCertificate.provider_id == row.id)
                .limit(1)
            ):
                raise CertificateError("DNS provider is still used by a certificate")
            db.delete(row)

    def create(self, payload):
        self.check_challenge(payload)
        if payload.directory_url not in self.settings.certificate_acme_directories:
            raise CertificateError("ACME directory is not enabled by the host administrator")
        if bool(payload.eab_kid) != bool(payload.eab_hmac_key):
            raise CertificateError("Both EAB account fields are required")
        eab = (
            self.vault.seal(
                {
                    "kid": payload.eab_kid.get_secret_value(),
                    "hmac": payload.eab_hmac_key.get_secret_value(),
                }
            )
            if payload.eab_kid
            else None
        )
        with self.session.begin() as db:
            if payload.provider_id:
                self.get(db, DNSProvider, payload.provider_id)
            row = ManagedCertificate(
                id=str(uuid4()),
                name=payload.name,
                domains=payload.domains,
                email=str(payload.email),
                provider_id=str(payload.provider_id) if payload.provider_id else None,
                challenge_type=payload.challenge_type,
                webroot_id=payload.webroot_id,
                directory_url=payload.directory_url,
                auto_renew=payload.auto_renew,
                eab=eab,
            )
            db.add(row)
            db.flush()
            return public_row(row, {"eab"})

    def check_challenge(self, row):
        if not row.email or "/" in row.email or "\\" in row.email:
            raise CertificateError("ACME account email is missing or unsafe")
        if row.challenge_type not in self.capabilities()["challenge_types"]:
            raise CertificateError(
                "Certificate challenge type is not enabled by the host administrator"
            )
        if (
            row.challenge_type == "webroot"
            and row.webroot_id not in self.settings.certificate_webroots
        ):
            raise CertificateError("Certificate webroot is not enabled by the host administrator")

    def list(self):
        with self.session() as db:
            return [
                public_row(row, {"eab"})
                for row in db.scalars(
                    select(ManagedCertificate).order_by(ManagedCertificate.created_at.desc())
                )
            ]

    def detail(self, identifier):
        with self.session() as db:
            row = self.get(db, ManagedCertificate, identifier)
            versions = [
                public_row(v, {"encrypted_material"})
                for v in db.scalars(
                    select(CertificateVersion)
                    .where(CertificateVersion.certificate_id == row.id)
                    .order_by(CertificateVersion.created_at.desc())
                )
            ]
            jobs = [
                public_row(j)
                for j in db.scalars(
                    select(CertificateJob)
                    .where(CertificateJob.certificate_id == row.id)
                    .order_by(CertificateJob.created_at.desc())
                    .limit(30)
                )
            ]
            targets = []
            for target in db.scalars(
                select(CertificateTarget).where(CertificateTarget.certificate_id == row.id)
            ):
                command = db.get(CommandModel, target.command_id) if target.command_id else None
                targets.append(
                    {
                        **public_row(target),
                        "status": command.status if command else "pending",
                        "error": (command.result_error if command else None) or target.last_error,
                    }
                )
            return {
                "certificate": public_row(row, {"eab"}),
                "versions": versions,
                "jobs": jobs,
                "targets": targets,
            }

    def edit(self, identifier, payload):
        with self.session.begin() as db:
            row = self.get(db, ManagedCertificate, identifier)
            if payload.auto_renew and not row.directory_url:
                raise CertificateError(
                    "Imported certificates cannot renew without ACME configuration"
                )
            row.name, row.auto_renew = payload.name, payload.auto_renew
            return public_row(row, {"eab"})

    def delete(self, identifier):
        with self.session.begin() as db:
            row = self.get(db, ManagedCertificate, identifier)
            if row.active_job_id:
                raise CertificateError("Wait for the active certificate job before deletion")
            for model in (CertificateJob, CertificateVersion, CertificateTarget):
                for item in db.scalars(select(model).where(model.certificate_id == row.id)):
                    db.delete(item)
            db.delete(row)

    def publish(self, db, row, data):
        details = {key: value for key, value in data.items() if key not in {"cert_pem", "key_pem"}}
        version = CertificateVersion(
            id=str(uuid4()),
            certificate_id=row.id,
            encrypted_material=self.vault.seal(data),
            details=details,
        )
        db.add(version)
        row.version_id, row.not_before, row.expires_at = (
            version.id,
            data["not_before"],
            data["expires_at"],
        )
        row.status, row.last_error, row.next_attempt = "issued", None, self.next_check(row)
        return version

    def import_certificate(self, payload):
        data = material(payload.cert_pem, payload.key_pem.get_secret_value())
        with self.session.begin() as db:
            row = ManagedCertificate(
                id=str(uuid4()), name=payload.name, domains=data["domains"], auto_renew=False
            )
            db.add(row)
            self.publish(db, row, data)
            db.flush()
            return public_row(row, {"eab"})

    def export(self, identifier, version_id=None):
        with self.session() as db:
            row = self.get(db, ManagedCertificate, identifier)
            version = self.get(db, CertificateVersion, version_id or row.version_id)
            if version.certificate_id != row.id:
                raise CertificateError("Certificate version does not belong to this certificate")
            return self.vault.open(version.encrypted_material)

    def activate(self, identifier, version_id):
        with self.session.begin() as db:
            row = self.get(db, ManagedCertificate, identifier)
            version = self.get(db, CertificateVersion, version_id)
            if row.active_job_id or version.certificate_id != row.id:
                raise CertificateError("An idle certificate and matching version are required")
            data = self.vault.open(version.encrypted_material)
            material(data["cert_pem"], data["key_pem"], row.domains)
            row.version_id, row.not_before, row.expires_at = (
                version.id,
                data["not_before"],
                data["expires_at"],
            )
            row.status, row.last_error = "issued", None
            return public_row(row, {"eab"})

    @staticmethod
    def next_check(row):
        lifetime = (row.expires_at or 0) - (row.not_before or 0)
        return time() + min(3600, max(1, lifetime / 6))

    @staticmethod
    def due(row, now=None):
        now = time() if now is None else now
        if not row.expires_at or not row.not_before:
            return False
        lifetime = row.expires_at - row.not_before
        window = min(30 * 86400, lifetime / (2 if lifetime <= 10 * 86400 else 3))
        return row.expires_at - now <= window

    def queue(self, identifier, kind, force=False):
        if not self.capabilities()["available"]:
            raise CertificateError("Configure certificate_lego_binary on the control-plane host")
        with self.session.begin() as db:
            row = self.get(db, ManagedCertificate, identifier)
            if not row.directory_url:
                raise CertificateError("Imported certificates do not have ACME configuration")
            self.check_challenge(row)
            if kind == "renew" and not row.version_id:
                raise CertificateError("Issue the certificate before requesting renewal")
            if kind == "issue" and row.version_id:
                raise CertificateError("Use renewal for an already issued certificate")
            job = CertificateJob(id=str(uuid4()), certificate_id=row.id, kind=kind, force=force)
            result = db.execute(
                update(ManagedCertificate)
                .where(
                    ManagedCertificate.id == row.id,
                    ManagedCertificate.active_job_id.is_(None),
                )
                .values(active_job_id=job.id, status="queued", last_error=None)
            )
            if result.rowcount != 1:
                raise CertificateError("A certificate job is already active")
            db.add(job)
            db.flush()
            return public_row(job)

    def save_target(self, identifier, payload):
        with self.session.begin() as db:
            row = self.get(db, ManagedCertificate, identifier)
            self.get(db, ServerModel, payload.server_id)
            if not covers(row.domains, payload.domain):
                raise CertificateError("Deployment hostname is not covered by this certificate")
            target = db.scalar(
                select(CertificateTarget).where(
                    CertificateTarget.server_id == str(payload.server_id),
                    CertificateTarget.cert_name == payload.cert_name,
                )
            )
            if target is not None and target.certificate_id != row.id:
                raise CertificateError("A different certificate already owns this target filename")
            if target is None:
                target = CertificateTarget(id=str(uuid4()), certificate_id=row.id)
                db.add(target)
            target.server_id, target.domain, target.cert_name = (
                str(payload.server_id),
                payload.domain,
                payload.cert_name,
            )
            target.reload, target.auto_deploy = payload.reload, payload.auto_deploy
            db.flush()
            return public_row(target)

    def delete_target(self, identifier, target_id):
        with self.session.begin() as db:
            target = self.get(db, CertificateTarget, target_id)
            if target.certificate_id != str(identifier):
                raise CertificateError("Certificate target not found")
            db.delete(target)

    def deploy(self, identifier, target_id):
        with self.session.begin() as db:
            row = self.get(db, ManagedCertificate, identifier)
            target = self.get(db, CertificateTarget, target_id)
            if target.certificate_id != row.id or not row.version_id:
                raise CertificateError(
                    "An issued certificate and matching deployment target are required"
                )
            previous = db.get(CommandModel, target.command_id) if target.command_id else None
            if previous and previous.status in {"pending", "waiting", "leased"}:
                raise CertificateError("The preceding certificate deployment is still pending")
            version = self.get(db, CertificateVersion, row.version_id)
            data = self.vault.open(version.encrypted_material)
            material(data["cert_pem"], data["key_pem"], row.domains)
            server = self.get(db, ServerModel, target.server_id)
            scan = db.get(AgentScanResultModel, server.id)
            if not scan or not scan.nginx or scan.nginx.get("mode") != "managed":
                raise CertificateError("Read an owned Agent scan before certificate deployment")
            directory = scan.nginx["certificate_dir"].rstrip("/")
            command = self.inventory._create_command_model(
                db,
                server,
                AgentCommandCreate(
                    method="POST",
                    path="/api/child/cert/deploy",
                    timeout_ms=90000,
                    body={
                        "domain": target.domain,
                        "cert_pem": data["cert_pem"],
                        "key_pem": data["key_pem"],
                        "cert_path": f"{directory}/{target.cert_name}.pem",
                        "key_path": f"{directory}/{target.cert_name}.key",
                        "reload": target.reload,
                    },
                ),
            )
            claimed = db.execute(
                update(CertificateTarget)
                .where(
                    CertificateTarget.id == target.id,
                    CertificateTarget.command_id == target.command_id
                    if target.command_id
                    else CertificateTarget.command_id.is_(None),
                )
                .values(command_id=command.id, version_id=row.version_id, last_error=None)
            )
            if claimed.rowcount != 1:
                raise CertificateError("Another certificate deployment was queued concurrently")
            db.flush()
            return self.inventory._command_read(command)
