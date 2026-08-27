import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

DNS_FIELDS = {
    "cloudflare": ["CF_DNS_API_TOKEN", "CF_ZONE_API_TOKEN"],
    "alidns": ["ALICLOUD_ACCESS_KEY", "ALICLOUD_SECRET_KEY"],
    "tencentcloud": ["TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY"],
    "dnspod": ["DNSPOD_API_KEY"],
    "godaddy": ["GODADDY_API_KEY", "GODADDY_API_SECRET"],
    "namesilo": ["NAMESILO_API_KEY"],
    "httpreq": ["HTTPREQ_ENDPOINT", "HTTPREQ_USERNAME", "HTTPREQ_PASSWORD"],
}
DNS_REQUIRED = {
    name: fields[:1] if name in {"cloudflare", "httpreq"} else fields
    for name, fields in DNS_FIELDS.items()
}


def dns_name(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    wildcard = value.startswith("*.")
    host = value[2:] if wildcard else value
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("Invalid DNS name") from None
    if (
        len(host) > 253
        or not host
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
            for part in host.split(".")
        )
    ):
        raise ValueError("Invalid DNS name")
    return "*." + host if wildcard else host


class CertificateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class DNSProviderInput(CertificateInput):
    name: str = Field(min_length=1, max_length=120)
    provider: Literal[
        "cloudflare", "alidns", "tencentcloud", "dnspod", "godaddy", "namesilo", "httpreq"
    ]
    credentials: dict[str, SecretStr] = Field(min_length=1, max_length=8)


class CertificateCreate(CertificateInput):
    name: str = Field(min_length=1, max_length=120)
    domains: list[str] = Field(min_length=1, max_length=20)
    email: EmailStr
    provider_id: UUID
    directory_url: str = "https://acme-v02.api.letsencrypt.org/directory"
    accept_terms: Literal[True]
    auto_renew: bool = True
    eab_kid: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    eab_hmac_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)

    @field_validator("domains")
    @classmethod
    def names(cls, values):
        names = [dns_name(value) for value in values]
        if len(set(names)) != len(names):
            raise ValueError("Certificate DNS names must be distinct")
        return names

    @field_validator("directory_url")
    @classmethod
    def directory(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("ACME directory must be an HTTPS URL without credentials")
        return value


class CertificateUpdate(CertificateInput):
    name: str = Field(min_length=1, max_length=120)
    auto_renew: bool


class CertificateImport(CertificateInput):
    name: str = Field(min_length=1, max_length=120)
    cert_pem: str = Field(min_length=1, max_length=131072)
    key_pem: SecretStr = Field(min_length=1, max_length=131072)


class CertificateDeployment(CertificateInput):
    server_id: UUID
    domain: str = Field(max_length=253)
    cert_name: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_.-]+$")
    reload: Literal["none", "nginx", "xray", "both"] = "nginx"
    auto_deploy: bool = True

    @field_validator("domain")
    @classmethod
    def concrete_name(cls, value):
        value = dns_name(value)
        if value.startswith("*."):
            raise ValueError("A deployment requires a concrete hostname")
        return value


class CertificateJobRequest(CertificateInput):
    force: bool = False
