"""A single durable ACME worker using the operator's pinned lego v4 executable."""

import asyncio
import contextlib
import logging
import os
import signal
import sys
import traceback
from time import time

from sqlalchemy import select

from open_node.services.certificate_vault import material, private_path
from open_node.services.certificates import (
    CertificateError,
    CertificateJob,
    CertificateTarget,
    DNSProvider,
    ManagedCertificate,
)

log = logging.getLogger(__name__)


class CertificateWorker:
    def __init__(self, store, connections):
        self.store, self.connections = store, connections
        self.settings = store.settings

    def recover(self):
        with self.store.session.begin() as db:
            for job in db.scalars(select(CertificateJob).where(CertificateJob.status == "running")):
                job.status, job.finished_at = "interrupted", time()
                job.message = (
                    "ACME execution was interrupted; retry after inspecting the certificate"
                )
                row = db.get(ManagedCertificate, job.certificate_id)
                if row and row.active_job_id == job.id:
                    row.active_job_id, row.status, row.last_error = None, "failed", job.message
                    row.next_attempt = time() + 3600

    def schedule(self):
        with self.store.session() as db:
            due = [
                row.id
                for row in db.scalars(
                    select(ManagedCertificate).where(
                        ManagedCertificate.auto_renew.is_(True),
                        ManagedCertificate.provider_id.is_not(None),
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
                with self.store.session.begin() as db:
                    row = db.get(CertificateTarget, target)
                    if row:
                        row.last_error = str(exc)
                continue

    async def run(self):
        while True:
            try:
                # Inherited by lego, so a surviving child prevents a second worker after a crash.
                with self.store.vault.lock("worker.lock", blocking=False) as lock_fd:
                    self.recover()
                    while True:
                        await self.deploy_pending()
                        if self.settings.certificate_lego_binary:
                            self.schedule()
                            if await self.run_one(lock_fd):
                                continue
                        await asyncio.sleep(self.settings.certificate_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Certificate worker unavailable (%s)", type(exc).__name__)
                await asyncio.sleep(self.settings.certificate_poll_seconds)

    async def run_one(self, lock_fd):
        with self.store.session.begin() as db:
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
            row.status = "issuing" if job.kind == "issue" else "renewing"
            provider = self.store.get(db, DNSProvider, row.provider_id)
        try:
            data = await self.obtain(row, provider, job, lock_fd)
            with self.store.session.begin() as db:
                current = self.store.get(db, ManagedCertificate, row.id)
                active_job = self.store.get(db, CertificateJob, job.id)
                if data is not None:
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
                else "ACME job failed; existing certificate remains active"
            )
            with self.store.session.begin() as db:
                current = self.store.get(db, ManagedCertificate, row.id)
                active_job = self.store.get(db, CertificateJob, job.id)
                active_job.status = (
                    "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
                )
                active_job.message, active_job.finished_at = message, time()
                current.active_job_id, current.status = None, "failed"
                current.last_error, current.next_attempt = message, time() + 3600
            if isinstance(exc, asyncio.CancelledError):
                raise
        await self.deploy_pending()
        return True

    async def obtain(self, row, provider, job, lock_fd):
        if job.kind == "renew" and not job.force and not self.store.due(row):
            return None
        root = self.store.vault.root
        work = private_path(root, root / row.id)
        work.mkdir(mode=0o700, exist_ok=True)
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

        if not job.force and cert_file.exists() and key_file.exists():
            with contextlib.suppress(ValueError, OSError):
                recovered = read_candidate()
                if not current or recovered["serial"] != current["serial"]:
                    return recovered

        environment = {"PATH": os.defpath, "HOME": str(work), "LANG": "C.UTF-8"}
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
            row.email,
            "--accept-tos",
            "--key-type",
            "ec256",
            "--dns",
            provider.provider,
            "--http-timeout",
            "15",
            "--dns-timeout",
            "10",
            "--cert.timeout",
            "90",
            "--user-agent",
            "Open-Node/0.1",
        ]
        for domain in row.domains:
            args.extend(["--domains", domain])
        for resolver in self.settings.certificate_dns_resolvers:
            args.extend(["--dns.resolvers", resolver])
        if job.kind == "issue" or not cert_file.exists():
            args.append("run")
        elif job.force:
            args.extend(["renew", "--days", "9999", "--ari-disable", "--no-random-sleep"])
        else:
            args.extend(["renew", "--dynamic", "--no-random-sleep"])
        await self.execute(args, environment, work, lock_fd)
        data = read_candidate()
        return None if current and data["serial"] == current["serial"] else data

    async def execute(self, args, environment, work, lock_fd):
        # uvloop rejects Popen's umask option; exec preserves the lock FD and process group.
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            "import os,sys; os.umask(0o077); os.execv(sys.argv[1], sys.argv[1:])",
            *args,
            cwd=work,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            pass_fds=(lock_fd,),
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
                        "ACME validation or issuance failed; check DNS and CA settings"
                    )
        except TimeoutError:
            raise CertificateError(
                "ACME job timed out; existing certificate remains active"
            ) from None
        finally:

            async def cleanup():
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
                await task
                raise
