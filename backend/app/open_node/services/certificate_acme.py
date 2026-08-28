"""Bounded ACME account/contact and revocation operations using Certbot's client."""

import hashlib
import json
import sys
import traceback
from pathlib import Path
from urllib.parse import urlsplit

import josepy as jose
import requests
from acme import client, messages
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from open_node.services.certificate_vault import CertificateVault, private_path

ADMIN_ERRORS = {
    "account_key_missing": "The existing ACME account key is missing",
    "account_destination_conflict": "Another key or account already occupies the new storage name",
    "account_inactive": "The ACME account is not active",
    "eab_already_bound": "An established EAB binding cannot be changed",
    "contact_unconfirmed": "The CA did not confirm the requested account contact",
}


class AdministrationError(ValueError):
    pass


def fingerprint(data):
    certificate = x509.load_pem_x509_certificate(data["cert_pem"].encode())
    return certificate.fingerprint(hashes.SHA256()).hex()


def signing_key(pem):
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if isinstance(key, rsa.RSAPrivateKey):
        algorithm = jose.RS256
    elif isinstance(key, ec.EllipticCurvePrivateKey):
        algorithm = {
            "secp256r1": jose.ES256,
            "secp384r1": jose.ES384,
            "secp521r1": jose.ES512,
        }.get(key.curve.name)
    else:
        algorithm = None
    if algorithm is None:
        raise ValueError("Unsupported ACME signing key type")
    return jose.JWK.load(pem.encode()), algorithm


def https_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("ACME endpoints require HTTPS without URL credentials")
    return value


def account_paths(vault, work, directory, email):
    https_url(directory)
    if not email or "/" in email or "\\" in email:
        raise ValueError("Invalid ACME account storage name")
    host = urlsplit(directory).netloc.replace(":", "_")
    folder = private_path(vault.root, work / "accounts" / host / email)
    return (
        private_path(vault.root, folder / "account.json"),
        private_path(vault.root, folder / "keys" / (email + ".key")),
    )


class BoundedSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False

    def request(self, method, url, *args, **kwargs):
        https_url(url)
        kwargs.update(stream=True, allow_redirects=False)
        response = super().request(method, url, *args, **kwargs)
        content = bytearray()
        try:
            for block in response.iter_content(65536):
                content.extend(block)
                if len(content) > 1048576:
                    raise ValueError("ACME response exceeds its size limit")
        finally:
            response.close()
        response._content = bytes(content)
        response._content_consumed = True
        return response


def connect(request, pem):
    key, algorithm = signing_key(pem)
    network = client.ClientNetwork(
        key=key,
        alg=algorithm,
        verify_ssl=request.get("ca_file") or True,
        user_agent="Open-Node/0.1",
        timeout=15,
    )
    session = BoundedSession()
    session.headers.update(network.session.headers)
    network.session.close()
    network.session = session
    try:
        directory = client.ClientV2.get_directory(https_url(request["directory_url"]), network)
        return client.ClientV2(directory, network)
    except BaseException:
        network.session.close()
        raise


def update_account(vault, request):
    work = private_path(vault.root, Path(request["profile_work"]))
    email, storage_email = request["email"], request["storage_email"]
    account_file, key_file = account_paths(vault, work, request["directory_url"], storage_email)
    if not key_file.exists():
        if account_file.exists():
            raise AdministrationError("account_key_missing")
        return {"email": email, "storage_email": None, "registered": False}
    pem = vault.read(key_file).decode()
    acme = connect(request, pem)
    try:
        registration = messages.RegistrationResource(body=messages.Registration(), uri="")
        try:
            registration = acme.query_registration(registration)
        except messages.Error as exc:
            if exc.code != "accountDoesNotExist":
                raise
            # Preserve a key left by an unsuccessful registration. The old copy
            # remains private; future lego runs select only the new storage name.
            new_account, new_key = account_paths(vault, work, request["directory_url"], email)
            if new_account != account_file and new_account.exists():
                raise AdministrationError("account_destination_conflict") from None
            if new_key.exists() and vault.read(new_key) != pem.encode():
                raise AdministrationError("account_destination_conflict") from None
            vault.write(new_key, pem.encode())
            return {"email": email, "storage_email": email, "registered": False}
        if registration.body.status != "valid":
            raise AdministrationError("account_inactive")
        if request["eab_action"] != "keep":
            raise AdministrationError("eab_already_bound")
        contact = ("mailto:" + email,)
        if tuple(registration.body.contact) != contact:
            registration = acme.update_registration(
                registration.update(body=messages.Registration()),
                messages.Registration(contact=contact),
            )
        if tuple(registration.body.contact) != contact:
            raise AdministrationError("contact_unconfirmed")
        https_url(registration.uri)
        # The client decodes EAB as an immutable mapping which its JSON encoder
        # cannot round-trip. Keep the CA proof locally; never resend it on update.
        body = json.loads(registration.body.update(external_account_binding=None).json_dumps())
        if registration.body.external_account_binding:
            body["externalAccountBinding"] = dict(registration.body.external_account_binding)
        vault.write(
            account_file,
            json.dumps(
                {
                    "email": email,
                    "registration": {"uri": registration.uri, "body": body},
                }
            ).encode(),
        )
        return {"email": email, "storage_email": storage_email, "registered": True}
    finally:
        acme.net.session.close()


def revoke_certificate(request):
    data = request["material"]
    certificate = x509.load_pem_x509_certificate(data["cert_pem"].encode())
    acme = connect(request, data["key_pem"])
    try:
        already = False
        try:
            # RFC 8555 permits proof of possession with the certificate key.
            # This also supports imported certificates without an ACME account.
            acme.revoke(certificate, request["reason"])
        except messages.Error as exc:
            if exc.code != "alreadyRevoked":
                raise
            already = True
        return {"fingerprint": fingerprint(data), "already_revoked": already}
    finally:
        acme.net.session.close()


def main():
    vault = CertificateVault(Path(sys.argv[1]))
    request_path = Path(sys.argv[2])
    raw = vault.read(request_path, 1048576)
    request = json.loads(raw)
    receipt = {
        "job_id": request["job_id"],
        "request_digest": hashlib.sha256(raw).hexdigest(),
    }
    try:
        if request["kind"] not in {"account", "revoke"}:
            raise ValueError("Unsupported administration operation")
        result = (
            update_account(vault, request)
            if request["kind"] == "account"
            else revoke_certificate(request)
        )
        receipt.update(status="succeeded", **result)
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, messages.Error)
            else str(exc)
            if isinstance(exc, AdministrationError)
            else None
        )
        if code not in {
            *ADMIN_ERRORS,
            "unauthorized",
            "badRevocationReason",
            "accountDoesNotExist",
            "serverInternal",
        }:
            code = "request_failed"
        receipt.update(status="failed", error_code=code)
        frames = " -> ".join(
            f"{frame.name}:{frame.lineno}" for frame in traceback.extract_tb(exc.__traceback__)
        )
        print("ACME administration failed:", type(exc).__name__, code, frames, file=sys.stderr)
    vault.write(request_path.with_name("result.json"), json.dumps(receipt).encode())


if __name__ == "__main__":
    main()
