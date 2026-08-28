import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

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
    challenge_type: Literal["dns", "standalone", "webroot"] = "dns"
    provider_id: UUID | None = None
    webroot_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    directory_url: str = "https://acme-v02.api.letsencrypt.org/directory"
    accept_terms: Literal[True]
    auto_renew: bool = True
    eab_kid: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    eab_hmac_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)

    @field_validator("accept_terms", mode="before")
    @classmethod
    def explicit_terms(cls, value):
        if value is not True:
            raise ValueError("Explicit acceptance of the CA terms is required")
        return value

    @field_validator("email")
    @classmethod
    def account_path(cls, value):
        if "/" in value or "\\" in value:
            raise ValueError("ACME account email cannot contain filesystem separators")
        return value

    @model_validator(mode="after")
    def challenge(self):
        if self.challenge_type == "dns":
            if not self.provider_id or self.webroot_id:
                raise ValueError("DNS validation requires only a DNS provider")
        else:
            if self.provider_id or any(name.startswith("*.") for name in self.domains):
                raise ValueError("HTTP validation cannot use DNS credentials or wildcard names")
            if (self.challenge_type == "webroot") != bool(self.webroot_id):
                raise ValueError("Only webroot validation requires a webroot ID")
        return self

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


class CertificateAccountUpdate(CertificateInput):
    email: EmailStr
    eab_action: Literal["keep", "replace", "remove"] = "keep"
    eab_kid: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    eab_hmac_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)

    @field_validator("email")
    @classmethod
    def account_path(cls, value):
        return CertificateCreate.account_path(value)

    @model_validator(mode="after")
    def binding(self):
        if self.eab_action == "replace":
            if not self.eab_kid or not self.eab_hmac_key:
                raise ValueError("Both replacement EAB credentials are required")
        elif self.eab_kid or self.eab_hmac_key:
            raise ValueError("EAB credentials require the replacement action")
        return self


class CertificateRevoke(CertificateInput):
    confirm: Literal[True]
    reason: Literal[0, 1, 3, 4, 5, 9] = 0
    directory_url: str | None = None

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit certificate revocation confirmation is required")
        return value

    @field_validator("reason", mode="before")
    @classmethod
    def strict_reason(cls, value):
        if type(value) is not int:
            raise ValueError("A numeric certificate revocation reason is required")
        return value

    @field_validator("directory_url")
    @classmethod
    def directory(cls, value):
        return CertificateCreate.directory(value) if value else value


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
