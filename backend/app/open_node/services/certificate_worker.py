"""A single durable ACME worker using the operator's pinned lego v4 executable."""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import signal
import sys
import traceback
from pathlib import Path
from time import time

from sqlalchemy import select

from open_node.services.backup_coordination import BackupWriteBarrier
from open_node.services.backup_runtime import backup_operation, current_backup_child_fds
from open_node.services.certificate_acme import ADMIN_ERRORS, fingerprint
from open_node.services.certificate_http import WebrootChallenges, harden_work
from open_node.services.certificate_remote import RemoteHTTP01
from open_node.services.certificate_vault import material, private_path
from open_node.services.certificates import (
    CertificateError,
    CertificateJob,
    CertificateRevocation,
    CertificateTarget,
    DNSProvider,
    ManagedCertificate,
)

log = logging.getLogger(__name__)


class CertificateWorker:
    def __init__(self, store, connections, *, backup_writes: BackupWriteBarrier | None = None):
        self.backup_writes = (
            backup_writes if backup_writes is not None else BackupWriteBarrier(None)
        )
        with backup_operation(self.backup_writes):
            self.store, self.connections = store, connections
            self.settings = store.settings
            self.webroots = WebrootChallenges(store.vault)
            self.remote = RemoteHTTP01(store, connections, backup_writes=self.backup_writes)

    def recover(self):
        with backup_operation(self.backup_writes):
            return self._recover()

    def _recover(self):
        self.webroots.recover()
        self.remote.request_cleanup()
        with self.store.write() as db:
            for job in db.scalars(select(CertificateJob).where(CertificateJob.status == "running")):
                row = db.get(ManagedCertificate, job.certificate_id)
                if (
                    row
                    and row.active_job_id == job.id
                    and (job.kind in {"account", "revoke"} or row.validation_server_id)
                ):
                    job.status, job.finished_at = "queued", None
                    job.message = "Resuming reconciliation with the CA"
                    row.status = "queued"
                    continue
                job.status, job.finished_at = "interrupted", time()
                job.message = (
                    "ACME execution was interrupted; retry after inspecting the certificate"
                )
                if row and row.active_job_id == job.id:
                    row.active_job_id, row.status, row.last_error = None, "failed", job.message
                    row.next_attempt = time() + 3600

    def schedule(self):
        with backup_operation(self.backup_writes):
            return self._schedule()

    def _schedule(self):
        with self.store.session() as db:
            due = [
                row.id
                for row in db.scalars(
                    select(ManagedCertificate).where(
                        ManagedCertificate.auto_renew.is_(True),
                        ManagedCertificate.directory_url.is_not(None),
                        ManagedCertificate.active_job_id.is_(None),
                        ManagedCertificate.next_attempt <= time(),
                    )
                )
                if self.store.due(row)
            ]
        for identifier in due:
            with contextlib.suppress(CertificateError):
                self.store.queue(identifier, "renew")

    async def deploy_pending(self):
        with backup_operation(self.backup_writes):
            return await self._deploy_pending()

    async def _deploy_pending(self):
        with self.store.session() as db:
            candidates = list(
                db.scalars(select(CertificateTarget).where(CertificateTarget.auto_deploy.is_(True)))
            )
            pending = [
                (target.certificate_id, target.id)
                for target in candidates
                if (row := db.get(ManagedCertificate, target.certificate_id))
                and row.version_id
                and row.version_id != target.version_id
            ]
        for identifier, target in pending:
            try:
                command = self.store.deploy(identifier, target)
                await self.connections.dispatch_command(self.store.inventory, command)
            except CertificateError as exc:
                with self.store.write() as db:
                    row = db.get(CertificateTarget, target)
                    if row:
                        row.last_error = str(exc)
                continue

    async def run(self):
        while True:
            try:
                # Inherited by lego, so a surviving child prevents a second worker after a crash.
                with contextlib.ExitStack() as locks:
                    # Opening worker.lock may create private state. Protect that
                    # short operation, not the worker lock's idle lifetime.
                    with backup_operation(self.backup_writes):
                        lock_fd = locks.enter_context(
                            self.store.vault.lock("worker.lock", blocking=False)
                        )
                    self.recover()
                    while True:
                        with backup_operation(self.backup_writes):
                            await self.deploy_pending()
                            await self.remote.drain()
                            self.schedule()
                            worked = await self.run_one(lock_fd)
                        if worked:
                            continue
                        await asyncio.sleep(self.settings.certificate_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Certificate worker unavailable (%s)", type(exc).__name__)
                await asyncio.sleep(self.settings.certificate_poll_seconds)

    async def run_one(self, lock_fd):
        with backup_operation(self.backup_writes):
            return await self._run_one(lock_fd)

    async def _run_one(self, lock_fd):
        with self.store.write() as db:
            job = db.scalar(
                select(CertificateJob)
                .where(CertificateJob.status == "queued")
                .order_by(CertificateJob.created_at)
                .limit(1)
            )
            if not job:
                return False
            job.status = "running"
            row = self.store.get(db, ManagedCertificate, job.certificate_id)
            row.status = {
                "issue": "issuing",
                "renew": "renewing",
                "account": "updating_account",
                "revoke": "revoking",
            }[job.kind]
        administration = job.kind in {"account", "revoke"}
        try:
            if administration:
                data = await self.administer(row, job, lock_fd)
            else:
                self.store.require_issuer(row)
                self.store.check_challenge(row)
                with self.store.session() as db:
                    provider = (
                        self.store.get(db, DNSProvider, row.provider_id)
                        if row.provider_id
                        else None
                    )
                data = (
                    await self.remote.obtain(row, job, lock_fd)
                    if row.validation_server_id
                    else await self.obtain(row, provider, job, lock_fd)
                )
            with self.store.write() as db:
                current = self.store.get(db, ManagedCertificate, row.id)
                active_job = self.store.get(db, CertificateJob, job.id)
                if administration:
                    if job.kind == "account":
                        self.store.apply_account(current, job, data)
                    else:
                        self.store.mark_revocation(db, job, "revoked")
                    current.status, current.last_error = self.store.state(db, current), None
                    active_job.status = "succeeded"
                    active_job.message = (
                        "CA reports this certificate is already revoked"
                        if data.get("already_revoked")
                        else None
                    )
                elif data is not None:
                    self.store.publish(db, current, data)
                    active_job.status = "succeeded"
                else:
                    current.status, current.next_attempt = "issued", self.store.next_check(current)
                    active_job.status = "skipped"
                    active_job.message = "The certificate is not due for renewal"
                active_job.finished_at = time()
                current.active_job_id = None
        except BaseException as exc:
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            log.warning(
                "Certificate job failed (%s) at %s:%s", type(exc).__name__, frame.name, frame.lineno
            )
            message = (
                str(exc)
                if isinstance(exc, CertificateError)
                else (
                    "CA administration did not finish; retry to reconcile its result"
                    if administration
                    else "ACME job failed; existing certificate material was retained"
                )
            )
            with self.store.write() as db:
                current = self.store.get(db, ManagedCertificate, row.id)
                active_job = self.store.get(db, CertificateJob, job.id)
                if (administration or row.validation_server_id) and isinstance(
                    exc, asyncio.CancelledError
                ):
                    active_job.status, active_job.finished_at = "queued", None
                    active_job.message = "Operation paused; reconciliation resumes after restart"
                    current.status = "queued"
                else:
                    active_job.status = (
                        "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
                    )
                    active_job.message, active_job.finished_at = message, time()
                    current.active_job_id = None
                    if job.kind == "revoke":
                        self.store.mark_revocation(db, job, "unknown")
                    current.status = self.store.state(
                        db, current, default=None if administration else "failed"
                    )
                    current.last_error = message
                    if not administration:
                        current.next_attempt = time() + 3600
            if isinstance(exc, asyncio.CancelledError):
                raise
        await self.deploy_pending()
        return True

    async def administer(self, row, job, lock_fd):
        with backup_operation(self.backup_writes):
            return await self._administer(row, job, lock_fd)

    async def _administer(self, row, job, lock_fd):
        root = self.store.vault.root
        work = private_path(root, root / row.id)
        directory = job.parameters.get("directory_url", row.directory_url)
        if directory not in self.settings.certificate_acme_directories:
            raise CertificateError("ACME directory is not enabled by the host administrator")
        request = {
            "job_id": job.id,
            "kind": job.kind,
            "directory_url": directory,
            "ca_file": str(self.settings.certificate_ca_file)
            if self.settings.certificate_ca_file
            else None,
            "profile_work": str(work),
        }
        if job.kind == "account":
            request.update(
                email=job.parameters["email"],
                storage_email=row.account_email or row.email,
                eab_action=job.parameters["eab_action"],
            )
        else:
            request.update(
                material=self.store.export(row.id, job.parameters["version_id"]),
                reason=job.parameters["reason"],
            )
        raw = json.dumps(request, sort_keys=True).encode()
        request_path = work / "jobs" / job.id / "request.json"
        result_path = request_path.with_name("result.json")
        self.store.vault.write(request_path, raw)
        digest = hashlib.sha256(raw).hexdigest()

        def read_receipt():
            data = json.loads(self.store.vault.read(result_path))
            if data.get("job_id") != job.id or data.get("request_digest") != digest:
                return None
            return data

        try:
            harden_work(work)
            data = read_receipt() if result_path.exists() else None
            if data is None:
                args = [
                    sys.executable,
                    "-P",
                    "-s",
                    "-m",
                    "open_node.services.certificate_acme",
                    str(root),
                    str(request_path),
                ]
                environment = {
                    "PATH": os.defpath,
                    "HOME": str(request_path.parent),
                    "LANG": "C.UTF-8",
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                }
                await self.execute(args, environment, request_path.parent, lock_fd)
                data = read_receipt()
            if not data or data.get("status") != "succeeded":
                code = data.get("error_code") if data else None
                if code in ADMIN_ERRORS:
                    raise CertificateError(ADMIN_ERRORS[code])
                suffix = (
                    f" ({code})"
                    if code
                    in {
                        "unauthorized",
                        "badRevocationReason",
                        "accountDoesNotExist",
                        "serverInternal",
                    }
                    else ""
                )
                raise CertificateError("CA result is not confirmed; retry to reconcile" + suffix)
            if job.kind == "revoke" and data.get("fingerprint") != job.parameters["fingerprint"]:
                raise CertificateError("CA revocation result does not match the certificate")
            return data
        finally:
            private_path(root, request_path).unlink(missing_ok=True)

    async def obtain(self, row, provider, job, lock_fd):
        with backup_operation(self.backup_writes):
            return await self._obtain(row, provider, job, lock_fd)

    async def _obtain(self, row, provider, job, lock_fd):
        if job.kind == "renew" and not job.force and not self.store.due(row):
            return None
        root = self.store.vault.root
        work = private_path(root, root / row.id)
        work.mkdir(mode=0o700, exist_ok=True)
        harden_work(work)
        filename = row.domains[0].replace("*.", "_.", 1)
        cert_file, key_file = (
            work / "certificates" / (filename + ".crt"),
            work / "certificates" / (filename + ".key"),
        )
        current = self.store.export(row.id) if row.version_id else None

        def read_candidate():
            return material(
                self.store.vault.read(cert_file).decode(),
                self.store.vault.read(key_file).decode(),
                row.domains,
            )

        unsafe_candidate = False
        if cert_file.exists() and key_file.exists():
            with contextlib.suppress(ValueError, OSError):
                recovered = read_candidate()
                with self.store.session() as db:
                    unsafe_candidate = (
                        db.get(CertificateRevocation, fingerprint(recovered)) is not None
                    )
                if (
                    not job.force
                    and not unsafe_candidate
                    and (not current or recovered["serial"] != current["serial"])
                ):
                    return recovered

        environment = {"PATH": os.defpath, "HOME": str(work), "LANG": "C.UTF-8"}
        if provider:
            environment.update(self.store.vault.open(provider.credentials))
        if self.settings.certificate_ca_file:
            environment["LEGO_CA_CERTIFICATES"] = str(self.settings.certificate_ca_file)
        if row.eab:
            eab = self.store.vault.open(row.eab)
            environment.update(LEGO_EAB="true", LEGO_EAB_KID=eab["kid"], LEGO_EAB_HMAC=eab["hmac"])
        args = [
            str(self.settings.certificate_lego_binary),
            "--path",
            str(work),
            "--server",
            row.directory_url,
            "--email",
            row.account_email or row.email,
            "--accept-tos",
            "--key-type",
            "ec256",
            "--http-timeout",
            "15",
            "--dns-timeout",
            "10",
            "--cert.timeout",
            "90",
            "--user-agent",
            "Open-Node/0.1",
        ]
        webroot = None
        if row.challenge_type == "dns":
            args.extend(["--dns", provider.provider])
            for resolver in self.settings.certificate_dns_resolvers:
                args.extend(["--dns.resolvers", resolver])
        elif row.challenge_type == "standalone":
            args.extend(["--http", "--http.port", self.settings.certificate_http_address])
        else:
            webroot = self.settings.certificate_webroots[row.webroot_id]
            self.webroots.prepare(webroot)
            args.extend(["--http", "--http.webroot", str(webroot)])
        for domain in row.domains:
            args.extend(["--domains", domain])
        if job.kind == "issue" or not cert_file.exists():
            args.append("run")
        elif job.force or unsafe_candidate:
            args.extend(["renew", "--days", "9999", "--ari-disable", "--no-random-sleep"])
        else:
            args.extend(["renew", "--dynamic", "--no-random-sleep"])
        try:
            await self.execute(args, environment, work, lock_fd)
        finally:
            harden_work(work)
            if webroot:
                self.webroots.cleanup(webroot)
        data = read_candidate()
        return None if current and data["serial"] == current["serial"] else data

    async def execute(self, args, environment, work, lock_fd):
        with backup_operation(self.backup_writes):
            return await self._execute(args, environment, work, lock_fd)

    async def _execute(self, args, environment, work, lock_fd):
        # uvloop rejects Popen's umask option; exec preserves the lock FD and process group.
        mask = "0o022" if "--http.webroot" in args else "0o077"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            f"import os,sys; os.umask({mask}); os.execv(sys.argv[1], sys.argv[1:])",
            *args,
            cwd=work,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            pass_fds=tuple(dict.fromkeys((lock_fd, *current_backup_child_fds()))),
        )
        output = bytearray()
        try:
            async with asyncio.timeout(self.settings.certificate_job_timeout):
                while block := await process.stdout.read(4096):
                    output.extend(block)
                    if len(output) > 262144:
                        raise CertificateError("ACME client exceeded its output limit")
                if await process.wait():
                    raise CertificateError(
                        "ACME validation or issuance failed; check HTTP/DNS routing and CA settings"
                    )
        except TimeoutError:
            raise CertificateError(
                "ACME job timed out; verify the result before retrying"
            ) from None
        finally:

            async def cleanup():
                # A copied ContextVar alone is not a retained lease. Keep a
                # reference for this task through child reaping and log writes.
                with backup_operation(self.backup_writes):
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    if process.returncode is None:
                        try:
                            await asyncio.wait_for(process.wait(), 5)
                        except TimeoutError:
                            with contextlib.suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                            await process.wait()
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    path = private_path(self.store.vault.root, work / "last-job.log")
                    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(output[:262144])

            task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Repeated cancellation must not interrupt cleanup or release
                # the operation while its child/tasks can still write.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                task.result()
                raise
