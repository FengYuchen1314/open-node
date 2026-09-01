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
from open_node.services.federation_crypto import (
    FEDERATION_ENCRYPTED_HEADER,
    FEDERATION_KEY_EXCHANGE_HEADER,
    FederationCryptoError,
    FederationSessionCache,
    derive_federation_session,
    generate_ephemeral,
)
from open_node.services.secure_channel import ChannelError, decode_public_key

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
    def __init__(self):
        self.sessions = FederationSessionCache()

    @staticmethod
    def _status(status, *, allow_not_found=False, allow_precondition=False):
        if status == 401:
            raise ServerSharingError(401, "server_share_token_invalid")
        if status == 403:
            raise ServerSharingError(403, "server_share_forbidden")
        if status == 404 and allow_not_found:
            return False
        if status == 412 and allow_precondition:
            return True
        if status >= 400:
            raise ServerSharingError(502, "server_share_owner_unavailable")
        return True

    def _request_bytes(
        self,
        owner_url,
        token,
        method,
        endpoint,
        encoded=None,
        *,
        content_type=None,
        extra_headers=None,
        allow_not_found=False,
        allow_precondition=False,
    ):
        owner = normalize_owner_url(owner_url)
        parts = urlsplit(owner)
        host, port = parts.hostname, parts.port or 443
        target = (parts.path.rstrip("/") + endpoint) or "/"
        headers = {
            "Accept": "application/json, application/octet-stream",
            "Accept-Encoding": "identity",
            "Host": parts.netloc,
            "User-Agent": "Open-Node-Federation/1",
            "X-Share-Token": token,
        }
        if encoded is not None:
            headers["Content-Type"] = content_type or "application/octet-stream"
            headers["Content-Length"] = str(len(encoded))
        headers.update(extra_headers or {})
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
                accepted = self._status(
                    response.status,
                    allow_not_found=allow_not_found,
                    allow_precondition=allow_precondition,
                )
                if not accepted:
                    return None
                return response.status, response.headers, content
            except ServerSharingError:
                raise
            except (OSError, urllib3.exceptions.HTTPError, ssl.SSLError) as exc:
                last_error = exc
            finally:
                if response is not None:
                    response.release_conn()
                pool.close()
        raise ServerSharingError(502, "server_share_owner_unavailable") from last_error

    def _request(
        self, owner_url, token, method, endpoint, body=None, *, allow_not_found=False
    ):
        encoded = None if body is None else json.dumps(
            body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        result = self._request_bytes(
            owner_url,
            token,
            method,
            endpoint,
            encoded,
            content_type="application/json" if encoded is not None else None,
            allow_not_found=allow_not_found,
        )
        if result is None:
            return None
        _status, _headers, content = result
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            raise ServerSharingError(
                502, "server_share_owner_response_invalid"
            ) from None

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
            # Keep the compatibility boundary explicit: consume every pinned
            # owner field represented by our contract and ignore future top-
            # level additions instead of rejecting an otherwise valid owner.
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

    @staticmethod
    def _legacy_payload(payload):
        body = b"" if payload.body is None else json.dumps(
            payload.body,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return json.dumps(
            {
                "method": payload.method,
                "path": payload.path,
                "body": base64.b64encode(body).decode("ascii"),
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _legacy_json(content):
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            raise ServerSharingError(
                502, "server_share_owner_response_invalid"
            ) from None

    @staticmethod
    def _session_key(owner_url, token):
        return normalize_owner_url(owner_url) + "\0" + token

    def _legacy_key_exchange(self, owner_url, token, encoded):
        private, consumer_public = generate_ephemeral()
        result = self._request_bytes(
            owner_url,
            token,
            "POST",
            "/api/federation/manage",
            encoded,
            content_type="application/json",
            extra_headers={
                FEDERATION_KEY_EXCHANGE_HEADER: base64.b64encode(
                    consumer_public
                ).decode("ascii")
            },
            allow_not_found=True,
        )
        if result is None:
            return None
        _status, headers, content = result
        owner_key = headers.get(FEDERATION_KEY_EXCHANGE_HEADER)
        if owner_key:
            try:
                owner_public = decode_public_key(owner_key)
                session = derive_federation_session(
                    private,
                    owner_public,
                    consumer_public,
                    token,
                    is_initiator=True,
                )
            except (ChannelError, FederationCryptoError):
                session = None
            if session is not None:
                self.sessions.set(self._session_key(owner_url, token), session)
        return self._legacy_json(content)

    def _legacy_manage(self, owner_url, token, payload):
        encoded = self._legacy_payload(payload)
        cache_key = self._session_key(owner_url, token)
        session = self.sessions.get(cache_key)
        if session is None:
            return self._legacy_key_exchange(owner_url, token, encoded)
        try:
            encrypted = session.encrypt(encoded)
            result = self._request_bytes(
                owner_url,
                token,
                "POST",
                "/api/federation/manage",
                encrypted,
                content_type="application/octet-stream",
                extra_headers={FEDERATION_ENCRYPTED_HEADER: "1"},
                allow_not_found=True,
                allow_precondition=True,
            )
            if result is None:
                self.sessions.delete(cache_key)
                return None
            status, headers, content = result
            if status == 412:
                raise ChannelError("Federation session expired")
            if headers.get(FEDERATION_ENCRYPTED_HEADER) == "1":
                content = session.decrypt(content)
            return self._legacy_json(content)
        except (ChannelError, FederationCryptoError):
            self.sessions.delete(cache_key)
            return self._legacy_key_exchange(owner_url, token, encoded)

    def manage(self, owner_url, token, payload: FederationCommandCreate):
        try:
            result = self._legacy_manage(owner_url, token, payload)
            if result is not None:
                now = datetime.now(UTC)
                return FederationCommandRead(
                    id=uuid4(), method=payload.method, path=payload.path,
                    status="succeeded", result_status=200, result_body=result,
                    failed=False, created_at=now, completed_at=now,
                    license_required=False,
                )
            value = self._request(
                owner_url, token, "POST", "/api/v1/federation/manage",
                payload.model_dump(mode="json"),
            )
            return FederationCommandRead.model_validate(value)
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
