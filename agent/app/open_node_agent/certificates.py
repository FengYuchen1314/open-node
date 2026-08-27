import ipaddress
import re
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID

from open_node_agent.runtime import RuntimeFailure


def hostname(value: str) -> str:
    if not isinstance(value, str):
        raise RuntimeFailure("A domain is required")
    try:
        domain = value.strip().rstrip(".").encode("idna").decode().lower()
    except UnicodeError:
        raise RuntimeFailure("Invalid domain") from None
    if (
        len(domain) > 253
        or not domain
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in domain.split(".")
        )
    ):
        raise RuntimeFailure("Invalid domain")
    return domain


def validate_pair(domain: str, cert_pem: str, key_pem: str) -> dict:
    domain = hostname(domain)
    if not all(
        isinstance(value, str) and 0 < len(value) <= 131072 for value in (cert_pem, key_pem)
    ):
        raise RuntimeFailure("Certificate and key PEM must each be at most 128 KiB")
    try:
        chain = x509.load_pem_x509_certificates(cert_pem.encode())
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
        leaf = chain[0]
        encoding = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if leaf.public_key().public_bytes(*encoding) != key.public_key().public_bytes(*encoding):
            raise RuntimeFailure("Certificate and private key do not match")
        now = datetime.now(UTC)
        if any(not cert.not_valid_before_utc <= now < cert.not_valid_after_utc for cert in chain):
            raise RuntimeFailure("Certificate chain is expired or not yet valid")
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        try:
            address = ipaddress.ip_address(domain)
        except ValueError:
            address = None
        matches = address in san.get_values_for_type(x509.IPAddress) if address else False
        if address is None:
            for name in san.get_values_for_type(x509.DNSName):
                name = name.lower().rstrip(".")
                if name == domain or (
                    name.startswith("*.")
                    and domain.count(".") == name.count(".")
                    and domain.split(".", 1)[-1] == name[2:]
                ):
                    matches = True
        if not matches:
            raise RuntimeFailure("Certificate SAN does not cover the requested domain")
        try:
            usage = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        except x509.ExtensionNotFound:
            usage = None
        if usage is not None and ExtendedKeyUsageOID.SERVER_AUTH not in usage:
            raise RuntimeFailure("Certificate is not valid for TLS server authentication")
    except RuntimeFailure:
        raise
    except (ValueError, TypeError, IndexError, x509.ExtensionNotFound):
        raise RuntimeFailure(
            "Invalid certificate/key PEM or missing subject alternative name"
        ) from None
    return {
        "domain": domain,
        "expires_at": leaf.not_valid_after_utc.isoformat(),
        "serial_number": str(leaf.serial_number),
    }
