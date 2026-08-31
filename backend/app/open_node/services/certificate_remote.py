"""Durable controller-to-node HTTP-01 leases and isolated central ACME execution."""

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import sys
from pathlib import Path
from time import time
from uuid import uuid4

from sqlalchemy import select

from open_node.domain.inventory import AgentCommandCreate
from open_node.services.backup_coordination import BackupWriteBarrier
from open_node.services.backup_runtime import backup_operation, current_backup_child_fds
from open_node.services.certificate_remote_acme import ERRORS
from open_node.services.certificate_vault import material, private_path
from open_node.services.certificates import CertificateError, CertificateHTTPLease
from open_node.services.inventory import CommandModel, ServerModel

ENDPOINT = "/api/child/cert/http01"
TERMINAL = {"succeeded", "failed", "skipped"}


def confirmed(command, lease_id):
    body = command.result_body
    return (
        command.status == "succeeded"
        and isinstance(body, dict)
        and body.get("success") is True
        and body.get("lease_id") == lease_id
    )


class RemoteHTTP01:
    def __init__(self, store, connections, *, backup_writes: BackupWriteBarrier | None = None):
        self.backup_writes = (
            backup_writes if backup_writes is not None else BackupWriteBarrier(None)
        )
        with backup_operation(self.backup_writes):
            self.store, self.connections = store, connections

    def request_cleanup(self, job_id=None):
        with backup_operation(self.backup_writes):
            return self._request_cleanup(job_id)

    def _request_cleanup(self, job_id):
        with self.store.write() as db:
            query = select(CertificateHTTPLease).where(CertificateHTTPLease.released_at.is_(None))
            if job_id:
                query = query.where(CertificateHTTPLease.job_id == job_id)
            for lease in db.scalars(query):
                lease.cleanup_requested = True

    async def drain(self):
        with backup_operation(self.backup_writes):
            return await self._drain()

    async def _drain(self):
        with self.store.session() as db:
            identifiers = list(
                db.scalars(
                    select(CertificateHTTPLease.id).where(
                        CertificateHTTPLease.cleanup_requested.is_(True),
                        CertificateHTTPLease.released_at.is_(None),
                        CertificateHTTPLease.next_attempt <= time(),
                    )
                )
            )
        for identifier in identifiers:
            with self.store.write() as db:
                lease = db.get(CertificateHTTPLease, identifier)
                previous = (
                    db.get(CommandModel, lease.cleanup_command_id)
                    if lease.cleanup_command_id
                    else None
                )
                if previous and confirmed(previous, lease.id):
                    lease.released_at = time()
                    continue
                server = db.get(ServerModel, lease.server_id)
                if server is None:
                    lease.next_attempt = time() + 300
                    continue
                if previous and previous.status not in TERMINAL:
                    command = previous
                else:
                    command = self.store.inventory._create_command_model(
                        db,
                        server,
                        AgentCommandCreate(
                            method="DELETE",
                            path=ENDPOINT,
                            timeout_ms=30000,
                            body={
                                "lease_id": lease.id,
                                "expires_at": lease.presentation["expires_at"],
                            },
                        ),
                    )
                    db.flush()
                    lease.cleanup_command_id = command.id
                lease.next_attempt = time() + 5
                outgoing = self.store.inventory._command_read(command)
            with contextlib.suppress(TimeoutError, OSError):
                async with asyncio.timeout(5):
                    await self.connections.dispatch_command(self.store.inventory, outgoing)

    async def present(self, row, job, items):
        with backup_operation(self.backup_writes):
            return await self._present(row, job, items)

    async def _present(self, row, job, items):
        if (
            not isinstance(items, list)
            or not 1 <= len(items) <= 20
            or any(
                not isinstance(item, dict)
                or set(item) != {"domain", "token", "key_authorization"}
                or item["domain"] not in row.domains
                or not isinstance(item["token"], str)
                or not isinstance(item["key_authorization"], str)
                for item in items
            )
        ):
            raise CertificateError("Invalid ACME HTTP-01 presentation request")
        self.store.check_challenge(row)
        # A restarted order may reuse its tokens. Confirm prior cleanup before
        # issuing a fresh lease; late releases must never delete newer responses.
        async with asyncio.timeout(60):
            while True:
                await self.drain()
                with self.store.session() as db:
                    pending = db.scalar(
                        select(CertificateHTTPLease.id)
                        .where(
                            CertificateHTTPLease.certificate_id == row.id,
                            CertificateHTTPLease.released_at.is_(None),
                        )
                        .limit(1)
                    )
                if not pending:
                    break
                await asyncio.sleep(0.5)
        lease_id = str(uuid4())
        body = {
            "lease_id": lease_id,
            "expires_at": time() + min(self.store.settings.certificate_job_timeout + 45, 570),
            "mode": row.challenge_type,
            "webroot_id": row.webroot_id,
            "challenges": items,
        }
        with self.store.write() as db:
            server = self.store.get(db, ServerModel, row.validation_server_id)
            command = self.store.inventory._create_command_model(
                db,
                server,
                AgentCommandCreate(
                    method="PUT",
                    path=ENDPOINT,
                    body=body,
                    timeout_ms=30000,
                ),
            )
            db.flush()
            db.add(
                CertificateHTTPLease(
                    id=lease_id,
                    certificate_id=row.id,
                    job_id=job.id,
                    server_id=server.id,
                    presentation=body,
                    present_command_id=command.id,
                )
            )
            outgoing = self.store.inventory._command_read(command)
        await self.connections.dispatch_command(self.store.inventory, outgoing)
        async with asyncio.timeout(60):
            while True:
                with self.store.session() as db:
                    current = db.get(CommandModel, command.id)
                    if current is None or current.status in TERMINAL:
                        if current and confirmed(current, lease_id):
                            return
                        raise CertificateError(
                            "The validation node did not confirm its HTTP-01 response"
                        )
                await asyncio.sleep(0.25)

    async def obtain(self, row, job, lock_fd):
        with backup_operation(self.backup_writes):
            return await self._obtain(row, job, lock_fd)

    async def _obtain(self, row, job, lock_fd):
        if job.kind == "renew" and not job.force and not self.store.due(row):
            return None
        vault, settings = self.store.vault, self.store.settings
        if row.directory_url not in settings.certificate_acme_directories:
            raise CertificateError("ACME directory is not enabled by the host administrator")
        profile_work = private_path(vault.root, vault.root / row.id)
        request_path = profile_work / "jobs" / job.id / "request.json"
        result_path = request_path.with_name("result.json")
        request = {
            "job_id": job.id,
            "directory_url": row.directory_url,
            "ca_file": str(settings.certificate_ca_file) if settings.certificate_ca_file else None,
            "profile_work": str(profile_work),
            "domains": row.domains,
            "email": row.email,
            "storage_email": row.account_email or row.email,
            "eab": vault.open(row.eab) if row.eab else None,
            "timeout": min(settings.certificate_job_timeout, 480),
        }
        raw = json.dumps(request, sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        vault.write(request_path, raw)

        def receipt():
            if not result_path.exists():
                return None
            data = json.loads(vault.read(result_path, 524288))
            if data.get("job_id") != job.id or data.get("request_digest") != digest:
                raise CertificateError("Saved ACME result does not match this job")
            return data

        try:
            result = receipt()
            if not result or result.get("status") != "succeeded":
                await self.execute(request_path, row, job, lock_fd)
                result = receipt()
            if not result or result.get("status") != "succeeded":
                raise CertificateError(
                    ERRORS.get(
                        result.get("error_code") if result else None,
                        "Remote HTTP-01 issuance failed; check node routing and CA settings",
                    )
                )
            data = result["material"]
            return material(data["cert_pem"], data["key_pem"], row.domains)
        finally:
            # Persist cleanup intent even when cancellation interrupts the next await.
            self.request_cleanup(job.id)
            private_path(vault.root, request_path).unlink(missing_ok=True)
            await self.drain()

    async def execute(self, request_path, row, job, lock_fd):
        with backup_operation(self.backup_writes):
            return await self._execute(request_path, row, job, lock_fd)

    async def _execute(self, request_path, row, job, lock_fd):
        work = request_path.parent
        environment = {
            "PATH": os.defpath,
            "HOME": str(work),
            "LANG": "C.UTF-8",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            "import os,sys; os.umask(0o077); os.execv(sys.argv[1], sys.argv[1:])",
            sys.executable,
            "-P",
            "-s",
            "-m",
            "open_node.services.certificate_remote_acme",
            str(self.store.vault.root),
            str(request_path),
            cwd=work,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            pass_fds=tuple(dict.fromkeys((lock_fd, *current_backup_child_fds()))),
            limit=16384,
        )
        output = bytearray()

        async def errors():
            while block := await process.stderr.read(4096):
                output.extend(block)
                if len(output) > 262144:
                    raise CertificateError("ACME client exceeded its output limit")

        async def protocol():
            count = 0
            while line := await process.stdout.readline():
                count += 1
                if count > 1 or len(line) > 16384:
                    raise CertificateError("Invalid ACME presentation protocol")
                request = json.loads(line)
                if set(request) != {"operation", "challenges"} or request["operation"] != "present":
                    raise CertificateError("Invalid ACME presentation operation")
                await self.present(row, job, request["challenges"])
                process.stdin.write(b'{"success":true}\n')
                await process.stdin.drain()
            if await process.wait():
                raise CertificateError("The ACME client exited without confirming its result")

        tasks = [asyncio.create_task(errors()), asyncio.create_task(protocol())]
        try:
            async with asyncio.timeout(self.store.settings.certificate_job_timeout):
                await asyncio.gather(*tasks)
        except TimeoutError:
            raise CertificateError(
                "Remote HTTP-01 timed out; existing certificates were retained"
            ) from None
        finally:

            async def stop():
                with backup_operation(self.backup_writes):
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    self.store.vault.write(work / "last-job.log", bytes(output[:262144]))

            task = asyncio.create_task(stop())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                task.result()
                raise
