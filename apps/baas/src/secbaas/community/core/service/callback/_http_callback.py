# Copyright (c) 2004-2026, Ant Group.
# All Rights Reserved.

"""HTTP Callback — 实现 PostRunCallback 协议

在 Bot 执行完成后被 worker / dispatcher 调用，
根据 run_id 查库构造 CallbackPayload，通过 HTTP POST
发送到 metadata.callback_url 指定的地址。
对 5xx 响应重试一次，避免下游临时抖动导致回调丢失。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import httpx

from secbaas.community.logger import get_logger

from ._models import CallbackPayload, CallbackResult

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot_run import BotRunRepository

logger = get_logger("router")

_DEFAULT_TIMEOUT: float = 10.0
_IDENTITY_DIGEST_LENGTH = 12
_MAX_RESPONSE_CODE_LENGTH = 64
_RESPONSE_CODE_PATTERN = re.compile(
    rf"^[A-Za-z0-9._:-]{{1,{_MAX_RESPONSE_CODE_LENGTH}}}$"
)


def _identity_digest(value: str | None) -> str:
    """Return a stable, non-reversible correlation value for an identity."""
    if not value:
        return "-"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_IDENTITY_DIGEST_LENGTH]


def _endpoint_summary(url: str) -> str:
    """Keep callback logs useful without exposing URL credentials or queries."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "<invalid>"
    if not parsed.scheme or not hostname:
        return "<invalid>"
    host = hostname
    if port is not None:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}{parsed.path or '/'}"


def _response_code(response_body: str) -> str:
    """Extract a bounded response code without retaining the response body."""
    try:
        parsed_body = json.loads(response_body)
    except (TypeError, json.JSONDecodeError):
        return "-"
    if not isinstance(parsed_body, dict):
        return "-"

    body = cast(dict[str, object], parsed_body)
    code = body.get("code")
    if code is None:
        error = body.get("error")
        if isinstance(error, dict):
            code = cast(dict[str, object], error).get("code")
    if code is None:
        return "-"

    if isinstance(code, bool) or not isinstance(code, (str, int)):
        return "-"

    candidate = str(code).strip()
    if not _RESPONSE_CODE_PATTERN.fullmatch(candidate):
        return "-"
    return candidate


class HttpCallback:
    """HTTP callback 发送实现，直接实现 PostRunCallback 协议

    调用 __call__(run_id) 时：
    1. 从 run_repository 查出 BotRunRecord
    2. 从 record.metadata 中取 callback_url
    3. 构造 CallbackPayload 并通过 HTTP POST 发送

    对 5xx 响应重试一次。
    """

    def __init__(
        self,
        run_repository: BotRunRepository,
        *,
        default_timeout: float = _DEFAULT_TIMEOUT,
        origin: str | None = None,
    ) -> None:
        self._run_repository = run_repository
        self._default_timeout = default_timeout
        self._origin = origin

    async def __call__(self, run_id: str) -> None:
        """PostRunCallback 入口：查库、构造 payload、发送。"""
        record = self._run_repository.get_by_run_id(run_id=run_id)
        if record is None:
            logger.warning(
                "[callback] record not found: run_id_digest=%s",
                _identity_digest(run_id),
            )
            return

        metadata = record.metadata or {}
        result_extra = record.result_extra or {}
        url: str | None = metadata.get("callback_url")
        if not url:
            logger.warning(
                "[callback] no callback_url in metadata: run_id_digest=%s",
                _identity_digest(record.run_id),
            )
            return

        payload = CallbackPayload(
            run_id=record.run_id,
            bot_id=record.bot_id,
            status=record.status,
            result=record.result_content,
            error=record.error,
            metadata=metadata,
            session_id=result_extra.get("session_id"),
        )
        result = await self._send(url, payload)
        if not result.success:
            logger.error(
                "[callback] send failed: identity(run=%s, bot=%s, session=%s), "
                "endpoint=%s, status=%s, response_code=%s",
                _identity_digest(payload.run_id),
                _identity_digest(payload.bot_id),
                _identity_digest(payload.session_id),
                _endpoint_summary(url),
                result.status_code or "-",
                result.response_code or "-",
            )

    async def _send(
        self,
        url: str,
        payload: CallbackPayload,
    ) -> CallbackResult:
        """发送 callback，5xx 自动重试一次。"""
        result = await self._send_once(url, payload)
        if result.status_code is not None and result.status_code >= 500:
            logger.warning(
                "[callback] retrying after %s: identity(run=%s, bot=%s, session=%s), "
                "endpoint=%s, response_code=%s",
                result.status_code,
                _identity_digest(payload.run_id),
                _identity_digest(payload.bot_id),
                _identity_digest(payload.session_id),
                _endpoint_summary(url),
                result.response_code or "-",
            )
            return await self._send_once(url, payload)
        return result

    async def _send_once(
        self,
        url: str,
        payload: CallbackPayload,
    ) -> CallbackResult:
        """执行一次 POST，返回 CallbackResult。"""
        body = json.dumps(payload.to_dict(), ensure_ascii=False)
        headers = {
            "Content-Type": "application/json",
        }
        if self._origin:
            headers["Origin"] = self._origin

        try:
            async with httpx.AsyncClient(timeout=self._default_timeout) as client:
                resp = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as e:
            logger.error(
                "[callback] error: identity(run=%s, bot=%s, session=%s), "
                "endpoint=%s, error_type=%s",
                _identity_digest(payload.run_id),
                _identity_digest(payload.bot_id),
                _identity_digest(payload.session_id),
                _endpoint_summary(url),
                type(e).__name__,
            )
            return CallbackResult(success=False, message=str(e))

        response_code = _response_code(resp.text)
        logger.info(
            "[callback] response: identity(run=%s, bot=%s, session=%s), "
            "endpoint=%s, status=%s, response_code=%s",
            _identity_digest(payload.run_id),
            _identity_digest(payload.bot_id),
            _identity_digest(payload.session_id),
            _endpoint_summary(url),
            resp.status_code,
            response_code,
        )

        success = 200 <= resp.status_code < 300
        if success:
            logger.info(
                "[callback] sent: identity(run=%s, bot=%s, session=%s), "
                "endpoint=%s, status=%s",
                _identity_digest(payload.run_id),
                _identity_digest(payload.bot_id),
                _identity_digest(payload.session_id),
                _endpoint_summary(url),
                resp.status_code,
            )
        else:
            logger.error(
                "[callback] failed: identity(run=%s, bot=%s, session=%s), "
                "endpoint=%s, status=%s, response_code=%s",
                _identity_digest(payload.run_id),
                _identity_digest(payload.bot_id),
                _identity_digest(payload.session_id),
                _endpoint_summary(url),
                resp.status_code,
                response_code,
            )
        return CallbackResult(
            success=success,
            status_code=resp.status_code,
            message="" if success else f"HTTP {resp.status_code}: code={response_code}",
            response_code=response_code,
        )
