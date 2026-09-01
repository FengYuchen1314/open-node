"""Durable DDNS configuration, IP-drift detection and retry worker."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import or_, select

from open_node.domain.ddns import (
    DDNSConfig,
    DDNSError,
    DDNSProviderRead,
    DDNSServerRead,
    DDNSSyncRead,
    DDNSWorkspaceRead,
)
from open_node.services.backup_runtime import backup_operation
from open_node.services.certificate_vault import covers
from open_node.services.certificates import DNSProvider, ManagedCertificate
from open_node.services.ddns_providers import (
    SUPPORTED_DDNS_PROVIDERS,
    DNSProviderFailure,
    provider_client,
    split_fqdn,
)
from open_node.services.inventory import ServerModel

log = logging.getLogger(__name__)
LEASE_SECONDS = 75
RETRY_SECONDS = 300


def _utc(value):
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _valid_ip(value, version):
    try:
        parsed = ip_address((value or "").strip())
    except ValueError:
        return None
    return str(parsed) if parsed.version == version else None


class DDNSStore:
    def __init__(self, inventory, certificates, *, clock=None):
        self.inventory, self.certificates = inventory, certificates
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _server(db, identifier):
        row = db.get(ServerModel, str(identifier))
        if row is None:
            raise DDNSError(404, "ddns_server_not_found")
        return row

    @staticmethod
    def _provider(db, identifier):
        row = db.get(DNSProvider, str(identifier))
        if row is None:
            raise DDNSError(404, "ddns_provider_not_found")
        if row.provider not in SUPPORTED_DDNS_PROVIDERS:
            raise DDNSError(422, "ddns_provider_unsupported")
        return row

    def _read(self, row, providers, *, is_federated=False):
        provider = providers.get(row.ddns_provider_id)
        return DDNSServerRead(
            server_id=row.id, server_name=row.name, server_status=row.status,
            is_federated=is_federated,
            enabled=row.ddns_enabled, provider_id=row.ddns_provider_id,
            provider_name=provider.name if provider else None,
            provider_type=provider.provider if provider else None,
            pull_address=row.pull_address, pull_address_v6=row.pull_address_v6,
            ip_address=row.ip_address, ip_address_v6=row.ip_address_v6,
            ipv6_enabled=row.ipv6_enabled, last_synced_at=_utc(row.ddns_last_synced_at),
            last_error=row.ddns_last_error, pending=row.ddns_pending,
            revision=row.ddns_revision,
        )

    def workspace(self):
        with self.inventory._session_factory() as db:
            from open_node.services.server_sharing import FederatedServerModel

            providers = list(db.scalars(select(DNSProvider).order_by(DNSProvider.name)))
            provider_map = {row.id: row for row in providers}
            servers = list(db.scalars(select(ServerModel).order_by(ServerModel.name)))
            federated = set(db.scalars(select(FederatedServerModel.id)))
            return DDNSWorkspaceRead(
                servers=[
                    self._read(row, provider_map, is_federated=row.id in federated)
                    for row in servers
                ],
                providers=[DDNSProviderRead(
                    id=row.id, name=row.name, provider=row.provider,
                    supported=row.provider in SUPPORTED_DDNS_PROVIDERS,
                ) for row in providers],
            )

    def configure(self, identifier: UUID, value: DDNSConfig):
        now = self.clock()
        with self.inventory._session_factory.begin() as db:
            if self.inventory._engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = self._server(db, identifier)
            if row.ddns_revision != value.expected_revision:
                raise DDNSError(409, "ddns_revision_conflict")
            if value.provider_id is not None:
                self._provider(db, value.provider_id)
            if value.enabled:
                for domain in (value.pull_address, value.pull_address_v6):
                    if domain:
                        try:
                            split_fqdn(domain)
                        except DNSProviderFailure as exc:
                            raise DDNSError(422, exc.code) from None
            row.ddns_enabled = value.enabled
            row.ddns_provider_id = str(value.provider_id) if value.provider_id else None
            row.pull_address = value.pull_address
            row.pull_address_v6 = value.pull_address_v6
            row.ddns_revision += 1
            row.ddns_pending = False
            row.ddns_attempt_id = row.ddns_lease_until = None
            row.ddns_last_error = None
            row.ddns_force = value.enabled
            row.ddns_next_attempt_at = now if value.enabled else None
            row.updated_at = now
            provider_map = {}
            if row.ddns_provider_id:
                provider = self._provider(db, row.ddns_provider_id)
                provider_map[provider.id] = provider
            db.flush()
            from open_node.services.server_sharing import FederatedServerModel

            return self._read(
                row, provider_map,
                is_federated=db.get(FederatedServerModel, row.id) is not None,
            )

    def queue(self, identifier: UUID):
        now = self.clock()
        with self.inventory._session_factory.begin() as db:
            if self.inventory._engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = self._server(db, identifier)
            if not row.ddns_enabled:
                raise DDNSError(409, "ddns_not_enabled")
            if (
                row.ddns_pending and row.ddns_lease_until
                and _utc(row.ddns_lease_until) > now
            ):
                raise DDNSError(409, "ddns_busy")
            row.ddns_force, row.ddns_next_attempt_at = True, now
            row.ddns_last_error = None
            row.updated_at = now
            providers = {}
            if row.ddns_provider_id:
                provider = self._provider(db, row.ddns_provider_id)
                providers[provider.id] = provider
            db.flush()
            from open_node.services.server_sharing import FederatedServerModel

            return DDNSSyncRead(
                server=self._read(
                    row, providers,
                    is_federated=db.get(FederatedServerModel, row.id) is not None,
                ),
                queued=True,
            )

    def claim(self):
        now = self.clock()
        with self.inventory._session_factory.begin() as db:
            if self.inventory._engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            rows = db.scalars(
                select(ServerModel).where(
                    ServerModel.ddns_enabled.is_(True),
                    or_(ServerModel.ddns_next_attempt_at.is_(None),
                        ServerModel.ddns_next_attempt_at <= now),
                    or_(ServerModel.ddns_lease_until.is_(None),
                        ServerModel.ddns_lease_until <= now),
                ).order_by(ServerModel.ddns_next_attempt_at, ServerModel.name)
            )
            for row in rows:
                ipv4 = _valid_ip(row.ip_address, 4)
                ipv6 = _valid_ip(row.ip_address_v6, 6) if row.ipv6_enabled else None
                changed = row.ddns_last_ipv4 != ipv4 or row.ddns_last_ipv6 != ipv6
                if not (row.ddns_force or row.ddns_last_error or changed):
                    if row.ddns_pending:
                        row.ddns_pending = False
                    continue
                attempt = str(uuid4())
                row.ddns_attempt_id, row.ddns_pending, row.ddns_force = attempt, True, False
                row.ddns_lease_until = now + timedelta(seconds=LEASE_SECONDS)
                row.ddns_last_error = None
                db.flush()
                return {
                    "server_id": row.id, "revision": row.ddns_revision, "attempt": attempt,
                    "provider_id": row.ddns_provider_id, "pull_address": row.pull_address,
                    "pull_address_v6": row.pull_address_v6, "ipv4": ipv4, "ipv6": ipv6,
                }
        return None

    def _credentials(self, provider):
        try:
            value = self.certificates.vault.open(provider.credentials)
        except (InvalidToken, OSError, ValueError, TypeError):
            raise DNSProviderFailure("ddns_provider_credentials_invalid") from None
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(secret, str) for key, secret in value.items()
        ):
            raise DNSProviderFailure("ddns_provider_credentials_invalid")
        return value

    def _client(self, db, provider_id):
        provider = db.get(DNSProvider, provider_id)
        if provider is None:
            raise DNSProviderFailure("ddns_provider_not_found")
        return provider_client(provider.provider, self._credentials(provider))

    def _resolve_client(self, job, domain):
        with self.inventory._session_factory() as db:
            if job["provider_id"]:
                return self._client(db, job["provider_id"])
            for certificate in db.scalars(
                select(ManagedCertificate).where(ManagedCertificate.provider_id.is_not(None))
            ):
                if any(covers([name], domain) for name in certificate.domains):
                    return self._client(db, certificate.provider_id)
            for provider in db.scalars(select(DNSProvider).order_by(DNSProvider.name)):
                if provider.provider not in SUPPORTED_DDNS_PROVIDERS:
                    continue
                try:
                    client = provider_client(provider.provider, self._credentials(provider))
                    if client.can_manage(domain):
                        return client
                except DNSProviderFailure:
                    continue
        raise DNSProviderFailure("ddns_provider_cannot_manage")

    def execute(self, job):
        domain4, domain6 = job["pull_address"], job["pull_address_v6"]
        resolve_domain = domain4 or domain6
        if not resolve_domain:
            raise DNSProviderFailure("ddns_domain_invalid")
        client = self._resolve_client(job, resolve_domain)
        wrote = False
        if domain4 and job["ipv4"]:
            client.upsert(domain4, "A", job["ipv4"])
            wrote = True
        if job["ipv6"]:
            client.upsert(domain6 or domain4, "AAAA", job["ipv6"])
            wrote = True
        if not wrote:
            raise DNSProviderFailure("ddns_no_public_address")

    def finish(self, job, error=None):
        now = self.clock()
        with self.inventory._session_factory.begin() as db:
            if self.inventory._engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = db.get(ServerModel, job["server_id"])
            if row is None or row.ddns_attempt_id != job["attempt"]:
                return
            row.ddns_pending = False
            row.ddns_attempt_id = row.ddns_lease_until = None
            if row.ddns_revision != job["revision"] or not row.ddns_enabled:
                return
            if error:
                row.ddns_last_error = error
                row.ddns_next_attempt_at = now + timedelta(seconds=RETRY_SECONDS)
            else:
                row.ddns_last_error = None
                row.ddns_last_synced_at = now
                row.ddns_last_ipv4, row.ddns_last_ipv6 = job["ipv4"], job["ipv6"]
                row.ddns_next_attempt_at = now
            row.updated_at = now


class DDNSWorker:
    def __init__(self, store, *, backup_writes, poll_seconds=2):
        self.store, self.backup_writes, self.poll_seconds = store, backup_writes, poll_seconds

    def run_one(self):
        with backup_operation(self.backup_writes):
            job = self.store.claim()
            if job is None:
                return False
            try:
                self.store.execute(job)
            except DNSProviderFailure as exc:
                self.store.finish(job, exc.code)
            except Exception as exc:
                log.warning("DDNS update failed (%s)", type(exc).__name__)
                self.store.finish(job, "ddns_provider_unavailable")
            else:
                self.store.finish(job)
            return True

    async def run(self):
        while True:
            try:
                worked = await asyncio.to_thread(self.run_one)
                if not worked:
                    await asyncio.sleep(self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("DDNS worker unavailable (%s)", type(exc).__name__)
                await asyncio.sleep(self.poll_seconds)
