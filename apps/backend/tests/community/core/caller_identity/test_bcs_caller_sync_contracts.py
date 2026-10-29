"""Contract checks for the BCS caller-config sync port and its domain error."""

from __future__ import annotations

from agentclaw.community.core.errors import InternalError


def _import_contracts():
    from agentclaw.community.core.caller_identity import contracts

    return contracts


def _import_protocols():
    from agentclaw.community.core.caller_identity import protocols

    return protocols


class _StubSync:
    def sync_caller_config(
        self, *, bot_id: str, owner_id: str, enabled: bool, source_revision: int
    ) -> None:
        del bot_id, owner_id, enabled, source_revision


def test_bcs_caller_sync_protocol_is_satisfied_by_method_shape() -> None:
    protocols = _import_protocols()
    assert isinstance(_StubSync(), protocols.BcsCallerConfigSyncProtocol)


def test_caller_bcs_sync_error_is_an_internal_error_family_member() -> None:
    contracts = _import_contracts()
    assert issubclass(contracts.CallerBcsSyncError, InternalError)
    assert "CallerBcsSyncError" in contracts.__all__
    assert "BcsCallerConfigSyncProtocol" in _import_protocols().__all__
