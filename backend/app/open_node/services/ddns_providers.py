"""Bounded DNS-provider clients for A/AAAA dynamic updates."""

import base64
import hashlib
import hmac
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from ipaddress import ip_address
from secrets import token_hex
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from publicsuffix2 import get_sld

from open_node.domain.certificates import dns_name

SUPPORTED_DDNS_PROVIDERS = {
    "cloudflare", "alidns", "tencentcloud", "dnspod", "godaddy", "namesilo",
}
MAX_RESPONSE = 256 * 1024


class DNSProviderFailure(RuntimeError):
    """A stable internal category; remote bodies and credentials are never propagated."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def split_fqdn(value: str) -> tuple[str, str]:
    value = dns_name(value)
    try:
        ip_address(value)
    except ValueError:
        pass
    else:
        raise DNSProviderFailure("ddns_domain_invalid") from None
    if "*." in value or "." not in value:
        raise DNSProviderFailure("ddns_domain_invalid")
    zone = get_sld(value, strict=True)
    if not zone or (value != zone and not value.endswith("." + zone)):
        raise DNSProviderFailure("ddns_domain_invalid")
    return zone, "@" if value == zone else value[: -(len(zone) + 1)]


def _read(request: Request) -> bytes:
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed provider origins
            content = response.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        try:
            exc.read(MAX_RESPONSE + 1)
        finally:
            exc.close()
        raise DNSProviderFailure("ddns_provider_rejected") from None
    except (URLError, TimeoutError, OSError):
        raise DNSProviderFailure("ddns_provider_unavailable") from None
    if len(content) > MAX_RESPONSE:
        raise DNSProviderFailure("ddns_provider_invalid_response")
    return content


def _json(request: Request) -> dict:
    try:
        value = json.loads(_read(request))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise DNSProviderFailure("ddns_provider_invalid_response") from None
    if not isinstance(value, dict):
        raise DNSProviderFailure("ddns_provider_invalid_response")
    return value


def _request(url: str, *, method="GET", headers=None, body=None) -> Request:
    content = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    return Request(url, data=content, method=method, headers=headers or {})


class DNSProviderClient(ABC):
    @abstractmethod
    def upsert(self, fqdn: str, record_type: str, content: str) -> None: ...

    @abstractmethod
    def can_manage(self, fqdn: str) -> bool: ...


class CloudflareClient(DNSProviderClient):
    origin = "https://api.cloudflare.com/client/v4"

    def __init__(self, credentials: dict):
        token = str(credentials.get("CF_DNS_API_TOKEN", "")).strip()
        if not token:
            raise DNSProviderFailure("ddns_provider_credentials_invalid")
        self.headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    def call(self, path, *, method="GET", body=None):
        value = _json(_request(self.origin + path, method=method, headers=self.headers, body=body))
        if value.get("success") is not True:
            raise DNSProviderFailure("ddns_provider_rejected")
        return value.get("result")

    def zone_id(self, fqdn):
        zone, _ = split_fqdn(fqdn)
        result = self.call("/zones?" + urlencode({"name": zone}))
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            raise DNSProviderFailure("ddns_provider_cannot_manage")
        identifier = result[0].get("id")
        if not isinstance(identifier, str) or not identifier:
            raise DNSProviderFailure("ddns_provider_invalid_response")
        return identifier

    def upsert(self, fqdn, record_type, content):
        zone = self.zone_id(fqdn)
        query = urlencode({"type": record_type, "name": fqdn})
        records = self.call(f"/zones/{quote(zone)}/dns_records?{query}")
        body = {"type": record_type, "name": fqdn, "content": content, "ttl": 120,
                "proxied": False}
        if isinstance(records, list) and records:
            record = records[0]
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                raise DNSProviderFailure("ddns_provider_invalid_response")
            if record.get("content") == content and record.get("ttl") == 120:
                return
            self.call(
                f"/zones/{quote(zone)}/dns_records/{quote(record['id'])}",
                method="PATCH", body=body,
            )
        else:
            self.call(f"/zones/{quote(zone)}/dns_records", method="POST", body=body)

    def can_manage(self, fqdn):
        self.zone_id(fqdn)
        return True


class GoDaddyClient(DNSProviderClient):
    origin = "https://api.godaddy.com/v1"

    def __init__(self, credentials):
        key = str(credentials.get("GODADDY_API_KEY", "")).strip()
        secret = str(credentials.get("GODADDY_API_SECRET", "")).strip()
        if not key or not secret:
            raise DNSProviderFailure("ddns_provider_credentials_invalid")
        self.headers = {
            "Authorization": f"sso-key {key}:{secret}", "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def upsert(self, fqdn, record_type, content):
        zone, sub = split_fqdn(fqdn)
        path = f"/domains/{quote(zone)}/records/{record_type}/{quote(sub)}"
        _read(_request(self.origin + path, method="PUT", headers=self.headers, body=[{
            "type": record_type, "name": sub, "data": content, "ttl": 600,
        }]))

    def can_manage(self, fqdn):
        zone, _ = split_fqdn(fqdn)
        _read(_request(self.origin + f"/domains/{quote(zone)}", headers=self.headers))
        return True


class DNSPodClient(DNSProviderClient):
    origin = "https://dnsapi.cn"

    def __init__(self, credentials):
        token = str(credentials.get("DNSPOD_API_KEY", "")).strip()
        if not token or "," not in token:
            raise DNSProviderFailure("ddns_provider_credentials_invalid")
        self.common = {"login_token": token, "format": "json", "lang": "en",
                       "error_on_empty": "no"}

    def call(self, action, params):
        encoded = urlencode({**self.common, **params}).encode()
        value = _json(Request(
            self.origin + "/" + action, data=encoded, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ))
        status = value.get("status")
        if not isinstance(status, dict) or str(status.get("code")) != "1":
            raise DNSProviderFailure("ddns_provider_rejected")
        return value

    def upsert(self, fqdn, record_type, content):
        zone, sub = split_fqdn(fqdn)
        value = self.call("Record.List", {"domain": zone, "sub_domain": sub})
        records = value.get("records") if isinstance(value.get("records"), list) else []
        record = next((item for item in records if isinstance(item, dict)
                       and item.get("name") == sub
                       and str(item.get("type", "")).upper() == record_type), None)
        if record and record.get("value") == content:
            return
        params = {"domain": zone, "sub_domain": sub, "record_type": record_type,
                  "record_line": "默认", "value": content}
        if record:
            identifier = record.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise DNSProviderFailure("ddns_provider_invalid_response")
            params["record_id"] = identifier
            self.call("Record.Modify", params)
        else:
            self.call("Record.Create", params)

    def can_manage(self, fqdn):
        zone, _ = split_fqdn(fqdn)
        self.call("Record.List", {"domain": zone})
        return True


class NameSiloClient(DNSProviderClient):
    origin = "https://www.namesilo.com/api"

    def __init__(self, credentials):
        self.key = str(credentials.get("NAMESILO_API_KEY", "")).strip()
        if not self.key:
            raise DNSProviderFailure("ddns_provider_credentials_invalid")

    def call(self, action, params):
        query = urlencode({"version": "1", "type": "xml", "key": self.key, **params})
        try:
            root = ElementTree.fromstring(_read(Request(f"{self.origin}/{action}?{query}")))
        except ElementTree.ParseError:
            raise DNSProviderFailure("ddns_provider_invalid_response") from None
        if root.findtext("./reply/code") != "300":
            raise DNSProviderFailure("ddns_provider_rejected")
        return root

    def upsert(self, fqdn, record_type, content):
        zone, sub = split_fqdn(fqdn)
        root = self.call("dnsListRecords", {"domain": zone})
        expected = zone if sub == "@" else f"{sub}.{zone}"
        record = next((row for row in root.findall("./reply/resource_record")
                       if row.findtext("host") == expected
                       and (row.findtext("type") or "").upper() == record_type), None)
        if record is not None and record.findtext("value") == content:
            return
        params = {"domain": zone, "rrhost": "" if sub == "@" else sub,
                  "rrvalue": content, "rrttl": "3600"}
        if record is not None:
            identifier = record.findtext("record_id")
            if not identifier:
                raise DNSProviderFailure("ddns_provider_invalid_response")
            self.call("dnsUpdateRecord", {**params, "rrid": identifier})
        else:
            self.call("dnsAddRecord", {**params, "rrtype": record_type})

    def can_manage(self, fqdn):
        zone, _ = split_fqdn(fqdn)
        self.call("dnsListRecords", {"domain": zone})
        return True


class AliDNSClient(DNSProviderClient):
    origin = "https://alidns.aliyuncs.com/"

    def __init__(self, credentials):
        self.key = str(credentials.get("ALICLOUD_ACCESS_KEY", "")).strip()
        self.secret = str(credentials.get("ALICLOUD_SECRET_KEY", "")).strip()
        if not self.key or not self.secret:
            raise DNSProviderFailure("ddns_provider_credentials_invalid")

    @staticmethod
    def encode(value):
        return quote(str(value), safe="~-._")

    def call(self, action, params):
        values = {
            "Format": "JSON", "Version": "2015-01-09", "AccessKeyId": self.key,
            "SignatureMethod": "HMAC-SHA1", "Timestamp": datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ), "SignatureVersion": "1.0", "SignatureNonce": token_hex(16),
            "Action": action, **params,
        }
        canonical = "&".join(
            f"{self.encode(key)}={self.encode(values[key])}" for key in sorted(values)
        )
        signable = "GET&%2F&" + self.encode(canonical)
        signature = base64.b64encode(hmac.new(
            (self.secret + "&").encode(), signable.encode(), hashlib.sha1,
        ).digest()).decode()
        value = _json(Request(
            self.origin + "?" + canonical + "&Signature=" + self.encode(signature)
        ))
        if "Code" in value:
            raise DNSProviderFailure("ddns_provider_rejected")
        return value

    def upsert(self, fqdn, record_type, content):
        zone, sub = split_fqdn(fqdn)
        value = self.call("DescribeDomainRecords", {
            "DomainName": zone, "RRKeyWord": sub, "TypeKeyWord": record_type,
        })
        records = value.get("DomainRecords", {}).get("Record", [])
        record = next((row for row in records if isinstance(row, dict)
                       and row.get("RR") == sub and row.get("Type") == record_type), None)
        if record and record.get("Value") == content:
            return
        if record:
            identifier = record.get("RecordId")
            if not isinstance(identifier, str) or not identifier:
                raise DNSProviderFailure("ddns_provider_invalid_response")
            self.call("UpdateDomainRecord", {
                "RecordId": identifier, "RR": sub, "Type": record_type, "Value": content,
            })
        else:
            self.call("AddDomainRecord", {
                "DomainName": zone, "RR": sub, "Type": record_type, "Value": content,
            })

    def can_manage(self, fqdn):
        zone, _ = split_fqdn(fqdn)
        self.call("DescribeDomainRecords", {"DomainName": zone, "PageSize": 1})
        return True


class TencentDNSClient(DNSProviderClient):
    origin = "https://dnspod.tencentcloudapi.com"
    host = "dnspod.tencentcloudapi.com"

    def __init__(self, credentials):
        self.identifier = str(credentials.get("TENCENTCLOUD_SECRET_ID", "")).strip()
        self.secret = str(credentials.get("TENCENTCLOUD_SECRET_KEY", "")).strip()
        if not self.identifier or not self.secret:
            raise DNSProviderFailure("ddns_provider_credentials_invalid")

    @staticmethod
    def digest(value):
        return hashlib.sha256(value).hexdigest()

    def call(self, action, body):
        content = json.dumps(body, separators=(",", ":")).encode()
        timestamp = int(datetime.now(UTC).timestamp())
        date = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
        canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{self.host}\n"
        signed_headers = "content-type;host"
        canonical_request = "\n".join([
            "POST", "/", "", canonical_headers, signed_headers, self.digest(content),
        ])
        scope = f"{date}/dnspod/tc3_request"
        signable = "\n".join([
            "TC3-HMAC-SHA256", str(timestamp), scope,
            self.digest(canonical_request.encode()),
        ])
        secret_date = hmac.new(
            ("TC3" + self.secret).encode(), date.encode(), hashlib.sha256
        ).digest()
        secret_service = hmac.new(secret_date, b"dnspod", hashlib.sha256).digest()
        secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
        signature = hmac.new(secret_signing, signable.encode(), hashlib.sha256).hexdigest()
        authorization = (
            "TC3-HMAC-SHA256 "
            f"Credential={self.identifier}/{scope}, SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        request = Request(self.origin, data=content, method="POST", headers={
            "Authorization": authorization, "Content-Type": "application/json; charset=utf-8",
            "Host": self.host, "X-TC-Action": action, "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": "2021-03-23",
        })
        response = _json(request).get("Response")
        if not isinstance(response, dict):
            raise DNSProviderFailure("ddns_provider_invalid_response")
        if "Error" in response:
            error = response.get("Error")
            if isinstance(error, dict) and error.get("Code") == "ResourceNotFound.NoDataOfRecord":
                return {"RecordList": []}
            raise DNSProviderFailure("ddns_provider_rejected")
        return response

    def upsert(self, fqdn, record_type, content):
        zone, sub = split_fqdn(fqdn)
        value = self.call("DescribeRecordList", {
            "Domain": zone, "Subdomain": sub, "RecordType": record_type,
        })
        records = value.get("RecordList") if isinstance(value.get("RecordList"), list) else []
        record = next((row for row in records if isinstance(row, dict)
                       and row.get("Name") == sub and row.get("Type") == record_type), None)
        if record and record.get("Value") == content:
            return
        fields = {"Domain": zone, "SubDomain": sub, "RecordType": record_type,
                  "RecordLine": "默认", "Value": content}
        if record:
            identifier = record.get("RecordId")
            if not isinstance(identifier, int):
                raise DNSProviderFailure("ddns_provider_invalid_response")
            self.call("ModifyRecord", {**fields, "RecordId": identifier})
        else:
            self.call("CreateRecord", fields)

    def can_manage(self, fqdn):
        zone, _ = split_fqdn(fqdn)
        self.call("DescribeRecordList", {"Domain": zone, "Limit": 1})
        return True


def provider_client(provider: str, credentials: dict) -> DNSProviderClient:
    clients = {
        "cloudflare": CloudflareClient,
        "alidns": AliDNSClient,
        "tencentcloud": TencentDNSClient,
        "dnspod": DNSPodClient,
        "godaddy": GoDaddyClient,
        "namesilo": NameSiloClient,
    }
    try:
        client = clients[provider]
    except KeyError:
        raise DNSProviderFailure("ddns_provider_unsupported") from None
    return client(credentials)
