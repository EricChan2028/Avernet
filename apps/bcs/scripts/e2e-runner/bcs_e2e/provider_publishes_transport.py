"""Dedicated bounded legacy product-HTTP transport for the Provider story."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from .correlation import RequestFailure, record_response_id
from .profile import ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM, identifier, unique_keys
from .provider_publishes_contract import STEP_BY_OPERATION
from .transport import tls_context


class ProviderPublishesTransportError(RequestFailure):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ProviderPublishesTransport:
    """Execute only the reviewed 42 legacy Provider operations.

    The transport receives the already-bound human ingress headers and the
    caller-specific minted token.  It never stores a token, Cookie value, raw
    response body, or arbitrary URL in its evidence record.
    """

    def __init__(self, profile: dict, *, human_headers: dict[str, str]):
        self.profile = profile
        self.base = profile["entrypoint"]["origin"].rstrip("/")
        self.human_headers = dict(human_headers)
        self.opener = build_opener(
            ProxyHandler({}), HTTPSHandler(context=tls_context()), _NoRedirect()
        )

    @staticmethod
    def _path_with_params(template: str, path_params: dict[str, str]) -> str:
        placeholders = set(re.findall(r"\{([a-z_]+)\}", template))
        if placeholders != set(path_params):
            raise ProviderPublishesTransportError(
                "provider route path parameters differ from the reviewed operation"
            )
        path = template
        for key in sorted(placeholders):
            value = path_params[key]
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ProviderPublishesTransportError(
                    "provider route path parameter is outside its reviewed bound"
                )
            path = path.replace("{" + key + "}", quote(value, safe=""))
        if "{" in path or "}" in path:
            raise ProviderPublishesTransportError(
                "provider route has an unresolved path parameter"
            )
        return path

    def call(
        self,
        operation: str,
        *,
        token: str | None,
        path_params: dict[str, str] | None = None,
        body: dict | None = None,
        provider_header_id: str | None = None,
    ) -> tuple[dict | None, dict]:
        try:
            row = STEP_BY_OPERATION[operation]
            binding = self.profile["route_bindings"][operation]
        except (KeyError, TypeError):
            raise ProviderPublishesTransportError(
                "provider operation has no reviewed route binding"
            ) from None
        if (
            binding["method"] != row["method"]
            or binding["path"] != row["path"]
            or binding["api_face"] != "legacy"
            or binding["auth"] != row["caller"]
            or binding["response"] != "legacy-json"
            or binding["traceparent"] != "disabled"
        ):
            raise ProviderPublishesTransportError(
                "provider route binding differs from the reviewed operation"
            )
        if provider_header_id is not None:
            identifier(provider_header_id, "provider_header_id")
            if not row["provider_header"]:
                raise ProviderPublishesTransportError(
                    "provider header is not admitted for this operation"
                )
        elif row["provider_header"]:
            raise ProviderPublishesTransportError(
                "provider operation requires the exact Provider header"
            )
        token_required = row["caller"] in {"provider_admin", "bot_runtime"}
        if token_required != isinstance(token, str):
            raise ProviderPublishesTransportError(
                "provider caller credential presence differs from the reviewed operation"
            )
        if token is not None and (
            not token
            or len(token) > 4096
            or not token.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in token)
        ):
            raise ProviderPublishesTransportError(
                "provider caller credential is outside its reviewed bounds"
            )
        path = self._path_with_params(row["path"], path_params or {})
        request_id = identifier("e2e-provider-" + uuid.uuid4().hex, "request_id")
        auth_headers = dict(self.human_headers)
        if token is not None:
            auth_headers["Authorization"] = "Bearer " + token
        headers = {
            **auth_headers,
            "Accept": "application/json",
            "X-Request-ID": request_id,
        }
        if provider_header_id is not None:
            headers["X-BCN-Provider-Id"] = provider_header_id
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        byte_budget = binding.get("max_response_bytes")
        if byte_budget is None:
            byte_budget = self.profile["policy"]["max_response_bytes"]
        if (
            type(byte_budget) is not int
            or not 1 <= byte_budget <= ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM
        ):
            raise ProviderPublishesTransportError(
                "provider response budget is outside the reviewed bound"
            )
        request = Request(
            self.base + path,
            method=row["method"],
            headers=headers,
            data=payload,
        )
        started = time.monotonic()
        record = {
            "operation": operation,
            "method": row["method"],
            "path": path,
            "submitted_request_id": request_id,
            "http_status": None,
            "expected_status": row["expected_status"],
            "caller_role": row["caller"],
            "credentials_sent": "Cookie" in auth_headers,
            "authorization_sent": "Authorization" in auth_headers,
            "mock_human_sent": "X-Mock-User-Id" in auth_headers,
            "provider_header_sent": provider_header_id is not None,
            "traceparent_mode": "disabled",
            "response_budget_bytes": byte_budget,
        }
        if token is not None:
            record["authorization_sha256"] = hashlib.sha256(token.encode()).hexdigest()
        if body is not None:
            record["body_sha256"] = hashlib.sha256(payload or b"").hexdigest()
        sensitive_values = tuple(
            value for header, value in auth_headers.items()
            if header in {"Authorization", "Cookie"}
        ) + ((token,) if token is not None else ())
        try:
            try:
                response = self.opener.open(
                    request, timeout=self.profile["policy"]["timeout_seconds"]
                )
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                header_id = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = header_id == request_id
                record_response_id(
                    record,
                    header_id,
                    source="HEADER",
                    sensitive_values=sensitive_values,
                )
                if response.code != row["expected_status"]:
                    raise ProviderPublishesTransportError(
                        f"provider operation {operation} returned HTTP {response.code}"
                    )
                raw = response.read(byte_budget + 1)
                if len(raw) > byte_budget:
                    raise ProviderPublishesTransportError(
                        "provider response exceeds its configured byte budget"
                    )
                if response.code == 204 or not raw:
                    return None, record
                payload_value = json.loads(
                    raw, object_pairs_hook=unique_keys
                )
                if payload_value is not None and not isinstance(payload_value, dict):
                    raise ProviderPublishesTransportError(
                        "provider response must be a JSON object"
                    )
                return payload_value, record
        except ProviderPublishesTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise ProviderPublishesTransportError(
                "provider network, TLS, timeout or JSON decoding error"
            ).with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
