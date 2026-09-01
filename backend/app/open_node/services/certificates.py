import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from time import time
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    Integer,
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
from open_node.services.certificate_acme import account_paths, fingerprint, signing_key
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
    account_email: Mapped[str | None] = mapped_column(String(320))
    provider_id: Mapped[str | None] = mapped_column(String(36))
    validation_server_id: Mapped[str | None] = mapped_column(String(36))
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
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class CertificateHTTPLease(CertificateBase):
    __tablename__ = "certificate_http_leases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    certificate_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    server_id: Mapped[str] = mapped_column(String(36))
    presentation: Mapped[dict] = mapped_column(JSON)
    present_command_id: Mapped[str] = mapped_column(String(36))
    cleanup_command_id: Mapped[str | None] = mapped_column(String(36))
    cleanup_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    released_at: Mapped[float | None] = mapped_column(Float)
    next_attempt: Mapped[float] = mapped_column(Float, default=0)


class CertificateVersion(CertificateBase):
    __tablename__ = "certificate_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    certificate_id: Mapped[str] = mapped_column(String(36), index=True)
    encrypted_material: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time)


class CertificateRevocation(CertificateBase):
    __tablename__ = "certificate_revocations"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(24))
    directory_url: Mapped[str] = mapped_column(String(1024))
    reason: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[float] = mapped_column(Float, default=time)
    updated_at: Mapped[float] = mapped_column(Float, default=time)
    confirmed_at: Mapped[float | None] = mapped_column(Float)


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
    excluded = {*exclude, "account_email", "parameters"}
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in excluded
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
                or db.scalar(
                    select(CertificateJob.id)
                    .where(CertificateJob.parameters["eab"].as_string().is_not(None))
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
            for table, additions in {
                "managed_certificates": {
                    "challenge_type": "VARCHAR(16) NOT NULL DEFAULT 'dns'",
                    "webroot_id": "VARCHAR(64)",
                    "account_email": "VARCHAR(320)",
                    "validation_server_id": "VARCHAR(36)",
                },
                "certificate_jobs": {"parameters": "JSON NOT NULL DEFAULT '{}'"},
                "certificate_versions": {"fingerprint": "VARCHAR(64)"},
            }.items():
                columns = {column["name"] for column in inspect(connection).get_columns(table)}
                for name, kind in additions.items():
                    if name not in columns:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {kind}"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_certificate_versions_fingerprint "
                    "ON certificate_versions (fingerprint)"
                )
            )

    @contextmanager
    def write(self):
        with self.session.begin() as db:
            # Lock before reading: revocation and deployment must not both
            # validate stale state and then commit conflicting actions.
            if self.engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield db

    def capabilities(self):
        binary = self.settings.certificate_lego_binary
        with self.session() as db:
            nodes = [
                {"id": scan.server_id, "name": server.name, **scan.http01}
                for scan, server in db.execute(
                    select(AgentScanResultModel, ServerModel)
                    .join(
                        ServerModel,
                        AgentScanResultModel.server_id == ServerModel.id,
                    )
                    .where(AgentScanResultModel.http01.is_not(None))
                )
                if scan.http01 and scan.http01.get("version") == 1
            ]
        return {
            "available": bool(binary and binary.is_file() and os.access(binary, os.X_OK)),
            "self_signed": True,
            "license_required": False,
            "account_management": os.name == "posix",
            "revocation": os.name == "posix",
            "remote_http_available": os.name == "posix",
            "validation_nodes": nodes,
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
        with self.write() as db:
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
        with self.write() as db:
            row = self.get(db, DNSProvider, identifier)
            if db.scalar(
                select(ServerModel.id)
                .where(ServerModel.ddns_provider_id == row.id)
                .limit(1)
            ):
                raise CertificateError("DNS provider is still used by server DDNS")
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
        with self.write() as db:
            if payload.provider_id:
                self.get(db, DNSProvider, payload.provider_id)
            if payload.validation_server_id:
                self.get(db, ServerModel, payload.validation_server_id)
            row = ManagedCertificate(
                id=str(uuid4()),
                name=payload.name,
                domains=payload.domains,
                email=str(payload.email),
                provider_id=str(payload.provider_id) if payload.provider_id else None,
                validation_server_id=str(payload.validation_server_id)
                if payload.validation_server_id
                else None,
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
        if row.validation_server_id:
            with self.session() as db:
                self.get(db, ServerModel, row.validation_server_id)
                scan = db.get(AgentScanResultModel, str(row.validation_server_id))
                capability = scan.http01 if scan else None
            if (
                row.challenge_type not in {"standalone", "webroot"}
                or not capability
                or capability.get("version") != 1
                or capability.get("cleanup_error")
                or (row.challenge_type == "standalone" and not capability.get("standalone"))
                or (
                    row.challenge_type == "webroot"
                    and row.webroot_id not in capability.get("webroots", [])
                )
            ):
                raise CertificateError("HTTP validation is not enabled or healthy on this node")
            return
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
                {
                    **public_row(v, {"encrypted_material"}),
                    "revocation": self.revocation(db, v),
                }
                for v in db.scalars(
                    select(CertificateVersion)
                    .where(CertificateVersion.certificate_id == row.id)
                    .order_by(CertificateVersion.created_at.desc())
                )
            ]
            jobs = [
                {
                    **public_row(j),
                    "cleanup_pending": db.scalar(
                        select(CertificateHTTPLease.id)
                        .where(
                            CertificateHTTPLease.job_id == j.id,
                            CertificateHTTPLease.released_at.is_(None),
                        )
                        .limit(1)
                    )
                    is not None,
                }
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
                "account": self.account_info(db, row),
            }

    def account_info(self, db, row):
        if not row.directory_url:
            return None
        state, uri = "not_registered", None
        try:
            account_file, key_file = account_paths(
                self.vault,
                self.vault.root / row.id,
                row.directory_url,
                row.account_email or row.email,
            )
            if account_file.exists():
                account = json.loads(self.vault.read(account_file))
                uri = (account.get("registration") or {}).get("uri")
                state = "registered" if uri and key_file.exists() else "unavailable"
            elif key_file.exists():
                state = "unconfirmed"
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            state, uri = "unavailable", None
        latest = db.scalar(
            select(CertificateJob)
            .where(CertificateJob.certificate_id == row.id, CertificateJob.kind == "account")
            .order_by(CertificateJob.created_at.desc())
            .limit(1)
        )
        pending = latest and latest.status in {"queued", "running", "failed", "interrupted"}
        return {
            "email": row.email,
            "state": state,
            "uri": uri,
            "eab_configured": bool(row.eab),
            "pending_email": latest.parameters.get("email") if pending else None,
            "retry_job_id": latest.id
            if latest and latest.status in {"failed", "interrupted"}
            else None,
        }

    @staticmethod
    def revocation(db, version):
        entry = db.get(CertificateRevocation, version.fingerprint) if version.fingerprint else None
        return public_row(entry) if entry else None

    def require_usable(self, db, version):
        if self.revocation(db, version):
            raise CertificateError(
                "This certificate has a pending, unconfirmed or completed revocation"
            )

    def state(self, db, row, default=None):
        version = db.get(CertificateVersion, row.version_id) if row.version_id else None
        entry = self.revocation(db, version) if version else None
        if entry:
            return {
                "pending": "revocation_pending",
                "unknown": "revocation_unknown",
                "revoked": "revoked",
            }[entry["status"]]
        return default or ("issued" if version else "idle")

    def edit(self, identifier, payload):
        with self.write() as db:
            row = self.get(db, ManagedCertificate, identifier)
            if payload.auto_renew and not row.directory_url:
                raise CertificateError(
                    "Imported certificates cannot renew without ACME configuration"
                )
            if payload.auto_renew and row.version_id:
                self.require_usable(db, self.get(db, CertificateVersion, row.version_id))
            row.name, row.auto_renew = payload.name, payload.auto_renew
            return public_row(row, {"eab"})

    def delete(self, identifier):
        with self.write() as db:
            row = self.get(db, ManagedCertificate, identifier)
            if row.active_job_id:
                raise CertificateError("Wait for the active certificate job before deletion")
            if db.scalar(
                select(CertificateHTTPLease.id)
                .where(
                    CertificateHTTPLease.certificate_id == row.id,
                    CertificateHTTPLease.released_at.is_(None),
                )
                .limit(1)
            ):
                raise CertificateError("Wait for remote HTTP challenge cleanup before deletion")
            for model in (
                CertificateHTTPLease,
                CertificateJob,
                CertificateVersion,
                CertificateTarget,
            ):
                for item in db.scalars(select(model).where(model.certificate_id == row.id)):
                    db.delete(item)
            db.delete(row)

    def publish(self, db, row, data):
        digest = fingerprint(data)
        if db.get(CertificateRevocation, digest):
            raise CertificateError("This certificate is already subject to revocation")
        details = {key: value for key, value in data.items() if key not in {"cert_pem", "key_pem"}}
        version = CertificateVersion(
            id=str(uuid4()),
            certificate_id=row.id,
            encrypted_material=self.vault.seal(data),
            details=details,
            fingerprint=digest,
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
        return self._import_material(payload.name, data)

    def generate_self_signed(self, payload):
        key = ec.generate_private_key(ec.SECP256R1())
        primary = payload.domains[0]
        subject = x509.Name([
            x509.NameAttribute(
                NameOID.COMMON_NAME,
                primary if len(primary.encode()) <= 64 else "Open Node self-signed server",
            )
        ])
        names = []
        for domain in payload.domains:
            try:
                name = x509.IPAddress(ip_address(domain))
            except ValueError:
                name = x509.DNSName(domain)
            names.append(name)
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=payload.valid_days))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False,
                    crl_sign=False, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        data = material(
            certificate.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            payload.domains,
        )
        data["self_signed"] = True
        return self._import_material(payload.name, data)

    def _import_material(self, name, data):
        with self.write() as db:
            row = ManagedCertificate(
                id=str(uuid4()), name=name, domains=data["domains"], auto_renew=False
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
        with self.write() as db:
            row = self.get(db, ManagedCertificate, identifier)
            version = self.get(db, CertificateVersion, version_id)
            if row.active_job_id or version.certificate_id != row.id:
                raise CertificateError("An idle certificate and matching version are required")
            self.require_usable(db, version)
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
        with self.write() as db:
            row = self.get(db, ManagedCertificate, identifier)
            self.require_issuer(row)
            if not row.directory_url:
                raise CertificateError("Imported certificates do not have ACME configuration")
            self.check_challenge(row)
            if kind == "renew" and not row.version_id:
                raise CertificateError("Issue the certificate before requesting renewal")
            if kind == "issue" and row.version_id:
                raise CertificateError("Use renewal for an already issued certificate")
            if row.version_id and self.revocation(
                db, self.get(db, CertificateVersion, row.version_id)
            ):
                force = True
            return public_row(self.claim(db, row, kind, force=force))

    def require_issuer(self, row):
        capability = "remote_http_available" if row.validation_server_id else "available"
        if not self.capabilities()[capability]:
            raise CertificateError("ACME issuance client is unavailable on the control-plane host")

    @staticmethod
    def claim(db, row, kind, *, force=False, parameters=None):
        job = CertificateJob(
            id=str(uuid4()),
            certificate_id=row.id,
            kind=kind,
            force=force,
            parameters=parameters or {},
        )
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
        return job

    def queue_account(self, identifier, payload):
        if not self.capabilities()["account_management"]:
            raise CertificateError("ACME account management requires a POSIX host")
        parameters = {"email": str(payload.email), "eab_action": payload.eab_action}
        if payload.eab_action == "replace":
            parameters["eab"] = self.vault.seal(
                {
                    "kid": payload.eab_kid.get_secret_value(),
                    "hmac": payload.eab_hmac_key.get_secret_value(),
                }
            )
        with self.write() as db:
            row = self.get(db, ManagedCertificate, identifier)
            if not row.directory_url:
                raise CertificateError("Imported certificates do not have an ACME account")
            if row.directory_url not in self.settings.certificate_acme_directories:
                raise CertificateError("ACME directory is not enabled by the host administrator")
            if payload.eab_action != "keep" and self.account_info(db, row)["state"] == "registered":
                raise CertificateError("An established EAB binding cannot be changed")
            return public_row(self.claim(db, row, "account", parameters=parameters))

    def retry_account(self, identifier, job_id):
        if not self.capabilities()["account_management"]:
            raise CertificateError("ACME account management requires a POSIX host")
        with self.write() as db:
            row = self.get(db, ManagedCertificate, identifier)
            latest = db.scalar(
                select(CertificateJob)
                .where(
                    CertificateJob.certificate_id == row.id,
                    CertificateJob.kind == "account",
                )
                .order_by(CertificateJob.created_at.desc())
                .limit(1)
            )
            if (
                not latest
                or latest.id != str(job_id)
                or latest.status not in {"failed", "interrupted"}
            ):
                raise CertificateError("Only the latest failed account update can be retried")
            if row.directory_url not in self.settings.certificate_acme_directories:
                raise CertificateError("ACME directory is not enabled by the host administrator")
            return public_row(self.claim(db, row, "account", parameters=dict(latest.parameters)))

    def matching_versions(self, db, data):
        digest = fingerprint(data)
        matches = []
        for version in db.scalars(
            select(CertificateVersion).where(
                (CertificateVersion.fingerprint == digest)
                | (
                    (CertificateVersion.fingerprint.is_(None))
                    & (CertificateVersion.details["serial"].as_string() == data["serial"])
                )
            )
        ):
            if version.fingerprint is None:
                version.fingerprint = fingerprint(self.vault.open(version.encrypted_material))
            if version.fingerprint == digest:
                matches.append(version)
        return digest, matches

    def queue_revocation(self, identifier, version_id, payload):
        if not self.capabilities()["revocation"]:
            raise CertificateError("ACME revocation requires a POSIX host")
        with self.write() as db:
            row = self.get(db, ManagedCertificate, identifier)
            version = self.get(db, CertificateVersion, version_id)
            if version.certificate_id != row.id:
                raise CertificateError("Certificate version does not belong to this certificate")
            if version.details.get("self_signed") is True:
                raise CertificateError("Self-signed certificates cannot be revoked through ACME")
            directory = row.directory_url or payload.directory_url
            if (
                payload.directory_url
                and row.directory_url
                and payload.directory_url != row.directory_url
            ):
                raise CertificateError("Use the certificate profile's original ACME directory")
            if not directory or directory not in self.settings.certificate_acme_directories:
                raise CertificateError("Select an ACME directory enabled by the host administrator")
            data = self.vault.open(version.encrypted_material)
            signing_key(data["key_pem"])
            digest, versions = self.matching_versions(db, data)
            entry = db.get(CertificateRevocation, digest)
            if entry and entry.status in {"pending", "revoked"}:
                raise CertificateError("Certificate revocation is already pending or confirmed")
            for item in versions:
                profile = self.get(db, ManagedCertificate, item.certificate_id)
                if profile.active_job_id:
                    raise CertificateError("Wait for active jobs on all copies of this certificate")
            for target in db.scalars(
                select(CertificateTarget).where(
                    CertificateTarget.version_id.in_([item.id for item in versions])
                )
            ):
                command = db.get(CommandModel, target.command_id) if target.command_id else None
                if command and command.status in {"pending", "waiting", "leased"}:
                    raise CertificateError(
                        "Wait for pending certificate deployments before revocation"
                    )
            # Targets may have been removed by an older release. The retained
            # command still carries the exact PEM that could reach an Agent.
            for command in db.scalars(
                select(CommandModel).where(
                    CommandModel.path == "/api/child/cert/deploy",
                    CommandModel.status.in_(["pending", "waiting", "leased"]),
                )
            ):
                body = command.body
                if isinstance(body, dict) and isinstance(body.get("cert_pem"), str):
                    try:
                        matches = fingerprint(body) == digest
                    except ValueError:
                        continue
                    if matches:
                        raise CertificateError(
                            "Wait for pending certificate deployments before revocation"
                        )
            job = self.claim(
                db,
                row,
                "revoke",
                parameters={
                    "version_id": version.id,
                    "fingerprint": digest,
                    "directory_url": directory,
                    "reason": payload.reason,
                },
            )
            if entry is None:
                entry = CertificateRevocation(fingerprint=digest)
                db.add(entry)
            entry.status, entry.job_id = "pending", job.id
            entry.directory_url, entry.reason = directory, payload.reason
            entry.updated_at = time()
            db.flush()
            self.mark_revocation(db, job, "pending")
            return public_row(job)

    def mark_revocation(self, db, job, status):
        entry = self.get(db, CertificateRevocation, job.parameters["fingerprint"])
        if entry.job_id != job.id:
            raise CertificateError("A newer revocation attempt owns this certificate")
        entry.status, entry.updated_at = status, time()
        if status == "revoked":
            entry.confirmed_at = time()
        for row in db.scalars(
            select(ManagedCertificate)
            .join(CertificateVersion, ManagedCertificate.version_id == CertificateVersion.id)
            .where(CertificateVersion.fingerprint == entry.fingerprint)
        ):
            row.auto_renew = False
            if not row.active_job_id or row.active_job_id == job.id:
                row.status = self.state(db, row)

    def apply_account(self, row, job, receipt):
        if receipt["email"] != job.parameters["email"]:
            raise CertificateError("ACME account result does not match the requested contact")
        row.email, row.account_email = receipt["email"], receipt["storage_email"]
        if job.parameters["eab_action"] != "keep":
            if receipt["registered"]:
                raise CertificateError("An established EAB binding cannot be changed")
            row.eab = job.parameters.get("eab")

    def save_target(self, identifier, payload):
        with self.write() as db:
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
        with self.write() as db:
            target = self.get(db, CertificateTarget, target_id)
            if target.certificate_id != str(identifier):
                raise CertificateError("Certificate target not found")
            command = db.get(CommandModel, target.command_id) if target.command_id else None
            if command and command.status in {"pending", "waiting", "leased"}:
                raise CertificateError("Wait for the pending deployment before removing its target")
            db.delete(target)

    def deploy(self, identifier, target_id):
        with self.write() as db:
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
            self.require_usable(db, version)
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
