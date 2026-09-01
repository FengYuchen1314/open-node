"""Pinned-address HTTPS transport for controller-to-controller federation."""

import base64
import json
import ssl
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

import urllib3

from open_node.domain.server_sharing import (
    FederationCommandCreate,
    FederationCommandRead,
    FederationServerInfo,
    ServerSharingError,
)
from open_node.services.external_fetch import (
    ExternalFetchError,
    _resolve_public,
    normalize_external_url,
)

MAX_RESPONSE = 256 * 1024


def normalize_owner_url(value: str) -> str:
    try:
        normalized = normalize_external_url(value.rstrip("/") + "/")
        parts = urlsplit(normalized)
        if parts.query or parts.fragment:
            raise ValueError()
        return normalized.rstrip("/")
    except (ValueError, TypeError):
        raise ServerSharingError(422, "server_share_invalid_request") from None


class FederationHTTPTransport:
    def _request(
        self, owner_url, token, method, endpoint, body=None, *, allow_not_found=False
    ):
        owner = normalize_owner_url(owner_url)
        parts = urlsplit(owner)
        host, port = parts.hostname, parts.port or 443
        target = (parts.path.rstrip("/") + endpoint) or "/"
        encoded = None if body is None else json.dumps(
            body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Host": parts.netloc,
            "User-Agent": "Open-Node-Federation/1",
            "X-Share-Token": token,
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(encoded))
        context = ssl.create_default_context()
        last_error = None
        try:
            addresses = _resolve_public(host, port)
        except ExternalFetchError:
            raise ServerSharingError(502, "server_share_owner_unavailable") from None
        for _family, address in addresses:
            pool = urllib3.HTTPSConnectionPool(
                address, port=port, maxsize=1, block=True,
                assert_hostname=host, server_hostname=host, ssl_context=context,
            )
            response = None
            try:
                response = pool.urlopen(
                    method, target, body=encoded, headers=headers, redirect=False,
                    retries=False, preload_content=False,
                    timeout=urllib3.Timeout(connect=5.0, read=15.0),
                )
                content = response.read(MAX_RESPONSE + 1)
                if len(content) > MAX_RESPONSE:
                    raise ServerSharingError(502, "server_share_owner_response_invalid")
                if response.status in {301, 302, 303, 307, 308}:
                    raise ServerSharingError(502, "server_share_owner_response_invalid")
                if response.status == 401:
                    raise ServerSharingError(401, "server_share_token_invalid")
                if response.status == 403:
                    raise ServerSharingError(403, "server_share_forbidden")
                if response.status == 404 and allow_not_found:
                    return None
                if response.status >= 400:
                    raise ServerSharingError(502, "server_share_owner_unavailable")
                try:
                    value = json.loads(content.decode("utf-8"))
                except (UnicodeError, ValueError, TypeError):
                    raise ServerSharingError(
                        502, "server_share_owner_response_invalid"
                    ) from None
                return value
            except ServerSharingError:
                raise
            except (OSError, urllib3.exceptions.HTTPError, ssl.SSLError) as exc:
                last_error = exc
            finally:
                if response is not None:
                    response.release_conn()
                pool.close()
        raise ServerSharingError(502, "server_share_owner_unavailable") from last_error

    def server_info(self, owner_url, token):
        try:
            value = self._request(
                owner_url, token, "GET", "/api/v1/federation/server-info",
                allow_not_found=True,
            )
            if value is None:
                value = self._request(
                    owner_url, token, "GET", "/api/federation/server-info"
                )
            if not isinstance(value, dict):
                raise ValueError()
            # The pinned owner also sends traffic_reset_day and probe_sys. They
            # are not part of this consumer snapshot yet, but must not make an
            # otherwise compatible official owner impossible to import.
            return FederationServerInfo.model_validate({
                key: value[key]
                for key in FederationServerInfo.model_fields
                if key in value
            })
        except ServerSharingError:
            raise
        except (ValueError, TypeError):
            raise ServerSharingError(
                502, "server_share_owner_response_invalid"
            ) from None

    def manage(self, owner_url, token, payload: FederationCommandCreate):
        try:
            value = self._request(
                owner_url, token, "POST", "/api/v1/federation/manage",
                payload.model_dump(mode="json"),
                allow_not_found=True,
            )
            if value is not None:
                return FederationCommandRead.model_validate(value)
            encoded_body = b"" if payload.body is None else json.dumps(
                payload.body, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            result = self._request(
                owner_url, token, "POST", "/api/federation/manage",
                {
                    "method": payload.method,
                    "path": payload.path,
                    "body": base64.b64encode(encoded_body).decode("ascii"),
                },
            )
            now = datetime.now(UTC)
            return FederationCommandRead(
                id=uuid4(), method=payload.method, path=payload.path,
                status="succeeded", result_status=200, result_body=result,
                failed=False, created_at=now, completed_at=now,
                license_required=False,
            )
        except ServerSharingError:
            raise
        except (ValueError, TypeError):
            raise ServerSharingError(
                502, "server_share_owner_response_invalid"
            ) from None

    def command(self, owner_url, token, command_id):
        try:
            return FederationCommandRead.model_validate(self._request(
                owner_url, token, "GET", f"/api/v1/federation/commands/{command_id}",
            ))
        except ServerSharingError:
            raise
        except (ValueError, TypeError):
            raise ServerSharingError(
                502, "server_share_owner_response_invalid"
            ) from None
