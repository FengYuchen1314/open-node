"""Central ACME client with durable orders and public HTTP-01 presentation RPC."""

import hashlib
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from acme import challenges, messages
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from open_node.services.certificate_acme import (
    account_paths,
    connect,
    https_url,
    save_registration,
)
from open_node.services.certificate_vault import CertificateVault, material, private_path

ERRORS = {
    "order_unconfirmed": "The CA order response was lost; inspect CA state before a new attempt",
    "order_invalid": "The CA rejected the order or its domain validation",
    "http_unavailable": "The CA did not offer HTTP-01 validation",
    "account_inactive": "The ACME account is not active",
    "account_key_missing": "The existing ACME account key is missing",
    "presentation_failed": "The validation node did not confirm its HTTP-01 response",
}


class RemoteIssueError(ValueError):
    pass


def key_pem():
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def account(vault, request):
    account_file, key_file = account_paths(
        vault,
        Path(request["profile_work"]),
        request["directory_url"],
        request["storage_email"],
    )
    if not key_file.exists():
        if account_file.exists():
            raise RemoteIssueError("account_key_missing")
        vault.write(key_file, key_pem())
    acme = connect(request, vault.read(key_file).decode())
    try:
        try:
            registration = acme.query_registration(
                messages.RegistrationResource(body=messages.Registration(), uri=""),
            )
        except messages.Error as exc:
            if exc.code != "accountDoesNotExist":
                raise
            eab = request.get("eab")
            binding = (
                messages.ExternalAccountBinding.from_data(
                    acme.net.key.public_key(),
                    eab["kid"],
                    eab["hmac"],
                    acme.directory,
                )
                if eab
                else None
            )
            registration = acme.new_account(
                messages.NewRegistration.from_data(
                    email=request["email"],
                    terms_of_service_agreed=True,
                    external_account_binding=binding,
                )
            )
        if registration.body.status != "valid":
            raise RemoteIssueError("account_inactive")
        save_registration(vault, account_file, request["email"], registration)
        return acme
    except BaseException:
        acme.net.session.close()
        raise


def present(items):
    print(json.dumps({"operation": "present", "challenges": items}), flush=True)
    line = sys.stdin.buffer.readline(4097)
    if len(line) > 4096 or not json.loads(line).get("success"):
        raise RemoteIssueError("presentation_failed")


def obtain(vault, request, work, presenter=present):
    key_file, csr_file = work / "certificate.key", work / "request.csr"
    if not key_file.exists():
        if csr_file.exists() or (work / "order.json").exists():
            raise ValueError("The order's private key is missing")
        vault.write(key_file, key_pem())
    pem = vault.read(key_file)
    key = serialization.load_pem_private_key(pem, password=None)
    if not csr_file.exists():
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([]))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(name) for name in request["domains"]]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        vault.write(csr_file, csr.public_bytes(serialization.Encoding.PEM))
    csr_pem = vault.read(csr_file)
    csr = x509.load_pem_x509_csr(csr_pem)
    if (
        not csr.is_signature_valid
        or csr.public_key() != key.public_key()
        or set(
            csr.extensions.get_extension_for_class(
                x509.SubjectAlternativeName,
            ).value.get_values_for_type(x509.DNSName)
        )
        != set(request["domains"])
    ):
        raise ValueError("The saved order CSR does not match this profile")
    digest = hashlib.sha256(csr_pem).hexdigest()
    acme = account(vault, request)
    try:
        saved = work / "order.json"
        intent = work / "new-order.intent"
        if not saved.exists():
            if intent.exists():
                raise RemoteIssueError("order_unconfirmed")
            vault.write(intent, request["job_id"].encode())
            # Persist Location before fetching authorizations. A lost response is
            # explicitly uncertain; never silently submit another order on resume.
            response = acme._post(
                acme.directory["newOrder"],
                messages.NewOrder(
                    identifiers=[
                        messages.Identifier(typ=messages.IDENTIFIER_FQDN, value=name)
                        for name in request["domains"]
                    ],
                ),
            )
            uri = https_url(response.headers["Location"])
            vault.write(
                saved,
                json.dumps(
                    {
                        "uri": uri,
                        "job_id": request["job_id"],
                        "csr_digest": digest,
                    }
                ).encode(),
            )
        record = json.loads(vault.read(saved))
        if record["job_id"] != request["job_id"] or record["csr_digest"] != digest:
            raise ValueError("Saved ACME order does not match this job")
        uri = https_url(record["uri"])
        body = messages.Order.from_json(acme._post_as_get(uri).json())
        if {item.value for item in body.identifiers} != set(request["domains"]):
            raise ValueError("CA order identifiers do not match this profile")
        order = messages.OrderResource(body=body, uri=uri, csr_pem=csr_pem, authorizations=[])
        deadline = datetime.now() + timedelta(seconds=request["timeout"])
        if body.status == messages.STATUS_PENDING:
            authorizations = [
                acme._authzr_from_response(acme._post_as_get(url), uri=url)
                for url in body.authorizations
            ]
            responses, items = [], []
            for authorization in authorizations:
                if authorization.body.status == messages.STATUS_VALID:
                    continue
                if authorization.body.status != messages.STATUS_PENDING:
                    raise RemoteIssueError("order_invalid")
                challenge = next(
                    (
                        item
                        for item in authorization.body.challenges
                        if isinstance(item.chall, challenges.HTTP01)
                    ),
                    None,
                )
                if challenge is None:
                    raise RemoteIssueError("http_unavailable")
                responses.append(challenge)
                items.append(
                    {
                        "domain": authorization.body.identifier.value,
                        "token": challenge.chall.encode("token"),
                        "key_authorization": challenge.chall.key_authorization(acme.net.key),
                    }
                )
            if items:
                presenter(items)
                for challenge in responses:
                    if challenge.status == messages.STATUS_PENDING:
                        acme.answer_challenge(challenge, challenge.response(acme.net.key))
            order = acme.poll_authorizations(order.update(authorizations=authorizations), deadline)
        elif body.status not in {
            messages.STATUS_READY,
            messages.STATUS_PROCESSING,
            messages.STATUS_VALID,
        }:
            raise RemoteIssueError("order_invalid")
        # poll_finalization handles ready, processing and valid orders, including
        # a prior finalization whose response was lost before controller restart.
        order = acme.poll_finalization(order, deadline)
        return material(order.fullchain_pem, pem.decode(), request["domains"])
    finally:
        acme.net.session.close()


def main():
    vault = CertificateVault(Path(sys.argv[1]))
    request_path = private_path(vault.root, Path(sys.argv[2]))
    raw = vault.read(request_path)
    request = json.loads(raw)
    receipt = {"job_id": request["job_id"], "request_digest": hashlib.sha256(raw).hexdigest()}
    try:
        data = obtain(vault, request, request_path.parent)
        receipt.update(status="succeeded", material=data)
    except Exception as exc:
        code = str(exc) if isinstance(exc, RemoteIssueError) else "request_failed"
        receipt.update(status="failed", error_code=code)
        frames = " -> ".join(
            f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(exc.__traceback__)
        )
        print("Remote HTTP-01 issuance failed:", type(exc).__name__, code, frames, file=sys.stderr)
    vault.write(request_path.with_name("result.json"), json.dumps(receipt).encode())


if __name__ == "__main__":
    main()
