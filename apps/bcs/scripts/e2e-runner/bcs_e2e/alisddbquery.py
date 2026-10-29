"""Replaceable alisddbquery observer adapter.

The runner depends on a small semantic client protocol rather than an MCP/CLI
package. A deployment integration can inject the client; Stories only see the
stable EvidenceProvider contract. The mcporter binding translates the observed
metadata wire schema into normalized internal responses; custom injected clients implement that normalized protocol.
Metadata discovery is not physical environment proof.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol
import json
import math
import subprocess

from . import alisd_wire
from .bounded_process import ProcessCaptureError, ProcessCaptureTimeout, capture
from .errors import has_cancellation, primary_error
from .evidence import ASSERTIONS, AlisdDbEvidenceProvider, EvidenceError, EvidencePlan
from .profile import ConfigError, identifier, unique_keys


class AlisdDbQueryError(RuntimeError):
    pass


class AlisdDbQueryTimeout(AlisdDbQueryError, TimeoutError):
    pass


def _safe_identifier(value: object, field: str) -> str:
    """Normalize profile validation failures to the client protocol error."""
    try:
        return identifier(value, field)
    except ConfigError:
        raise AlisdDbQueryError(f"{field} binding is invalid") from None


class AlisdDbQueryClient(Protocol):
    def db_list(self) -> dict[str, Any]: ...
    def table_list(self, database: str) -> dict[str, Any]: ...
    def table_describe(self, database: str, table: str) -> dict[str, Any]: ...
    def db_query(self, plan: EvidencePlan) -> dict[str, Any]: ...


class McporterAlisdDbQueryClient:
    """Read-only mcporter binding with a finite, shell-free tool surface.

    This class is an integration adapter, not automatic environment discovery.
    The composition root must supply the approved server name and executable.
    """
    def __init__(self, *, server: str, executable: tuple[str, ...] = ("mcporter",),
                 timeout_seconds: int = 30, max_output_bytes: int = 2_097_152,
                 runner: Callable[..., subprocess.CompletedProcess[str]] | None = None):
        self.server = _safe_identifier(server, "observer.server")
        if not executable or any(not isinstance(part, str) or not part for part in executable):
            raise AlisdDbQueryError("mcporter executable binding is invalid")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise AlisdDbQueryError("mcporter timeout is outside the supported bound")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 8 * 1024 * 1024:
            raise AlisdDbQueryError("mcporter output limit is outside the supported bound")
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.runner = runner

    def _call(self, tool: str, args: dict | None = None, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        if timeout_seconds is not None and (type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise AlisdDbQueryError("invalid remaining observation budget")
        timeout = self.timeout_seconds if timeout_seconds is None else min(self.timeout_seconds, timeout_seconds)
        _safe_identifier(tool, "observer.tool")
        command = [*self.executable, "call", f"{self.server}.{tool}"]
        if args is not None:
            command.extend(("--args", json.dumps(args, separators=(",", ":"))))
        command.extend(("--output", "json"))
        try:
            if self.runner is None:
                captured = capture(command, timeout_seconds=timeout,
                                   max_output_bytes=self.max_output_bytes)
                result = subprocess.CompletedProcess(command, captured.returncode,
                                                     captured.stdout.decode('utf-8'), '')
            else:
                # Injectable normalized process seam for adapter contract tests.
                result = self.runner(command, capture_output=True, text=True,
                                     timeout=timeout, check=False, shell=False)
        except (OSError, subprocess.SubprocessError, ProcessCaptureError, UnicodeError) as error:
            cause = primary_error(error)
            if has_cancellation(error):
                cancelled = KeyboardInterrupt()
                if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                    cancelled.child_shutdown_status = 'UNCONFIRMED'
                raise cancelled from None
            error_type = AlisdDbQueryTimeout if isinstance(cause, (ProcessCaptureTimeout, subprocess.TimeoutExpired)) else AlisdDbQueryError
            public_error = error_type("mcporter invocation failed")
            if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
                public_error.child_shutdown_status = "UNCONFIRMED"
            raise public_error from None
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if (len(stdout.encode("utf-8", errors="replace")) + len(stderr.encode("utf-8", errors="replace"))
                > self.max_output_bytes):
            raise AlisdDbQueryError("mcporter response exceeds the safety limit")
        if result.returncode != 0:
            raise AlisdDbQueryError("mcporter returned a non-zero status")
        try:
            payload = json.loads(stdout, object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError, ConfigError):
            raise AlisdDbQueryError("mcporter response is not valid JSON") from None
        if not isinstance(payload, dict):
            raise AlisdDbQueryError("mcporter response must be a JSON object")
        return payload

    def db_list(self) -> dict[str, Any]:
        try:
            return alisd_wire.database_list(self._call("db_list"))
        except alisd_wire.AlisdWireError as error:
            raise AlisdDbQueryError(str(error)) from None

    def table_list(self, database: str) -> dict[str, Any]:
        try:
            return alisd_wire.table_list(
                self._call("table_list", {"db": _safe_identifier(database, "database")}), database)
        except alisd_wire.AlisdWireError as error:
            raise AlisdDbQueryError(str(error)) from None

    def table_describe(self, database: str, table: str) -> dict[str, Any]:
        try:
            return alisd_wire.describe_table(
                self._call("table_describe", {"db": _safe_identifier(database, "database"),
                                               "table": _safe_identifier(table, "table")}), table)
        except alisd_wire.AlisdWireError as error:
            raise AlisdDbQueryError(str(error)) from None

    def delivery_query(self, plan, *, timeout_seconds: float) -> dict[str, Any]:
        from .delivery_observation import query_arguments
        # Independent finite read surface; it does not widen resource ASSERTIONS.
        return self._call("db_query", query_arguments(plan), timeout_seconds=timeout_seconds)

    def delivery_page_query(self, plan, *, after_id: int, limit: int, timeout_seconds: float):
        from .delivery_pagination import query_arguments
        return self._call('db_query', query_arguments(plan, after_id=after_id, limit=limit),
                          timeout_seconds=timeout_seconds)

    def scope_query(self, plan, *, group: bool, timeout_seconds: float, contract: str = 'retained-group-session-facts-v1') -> dict[str, Any]:
        from .scope_observation import query_arguments
        return self._call('db_query', query_arguments(plan, group=group, contract=contract), timeout_seconds=timeout_seconds)

    def producer_query(self, plan, *, timeout_seconds: float) -> dict[str, Any]:
        from .producer_inventory import query_arguments
        return self._call('db_query', query_arguments(plan), timeout_seconds=timeout_seconds)

    def downstream_query(self, plan, *, result: bool, timeout_seconds: float) -> dict[str, Any]:
        from .downstream_observation import query_arguments
        return self._call("db_query", query_arguments(plan, result=result), timeout_seconds=timeout_seconds)

    def db_query(self, plan: EvidencePlan) -> dict[str, Any]:
        if not isinstance(plan, EvidencePlan) or type(plan.limit) is not int or not 1 <= plan.limit <= 100:
            raise AlisdDbQueryError("evidence query plan is outside the bounded contract")
        expected = ASSERTIONS.get(plan.assertion_id)
        if expected is None:
            raise AlisdDbQueryError("evidence query assertion is not approved")
        expected_table, expected_fields, expected_key = expected
        database = _safe_identifier(plan.database, "database")
        table = _safe_identifier(plan.table, "table")
        selected = tuple(plan.select) if isinstance(plan.select, (tuple, list)) else ()
        if (table != expected_table or not selected or len(set(selected)) != len(selected)
                or any(field not in expected_fields for field in selected)):
            raise AlisdDbQueryError("evidence query projection is not approved")
        if not isinstance(plan.filters, dict) or set(plan.filters) != {"env", expected_key}:
            raise AlisdDbQueryError("evidence query filters are not approved")
        filters = [(_safe_identifier(key, "filter"),
                    _safe_identifier(value, "filter_value"))
                   for key, value in plan.filters.items()]
        if {key for key, _value in filters} != {"env", expected_key}:
            raise AlisdDbQueryError("evidence query filters are not approved")
        # Filters and projections originate from the fixed ASSERTIONS map and
        # validated identifiers; callers cannot supply arbitrary SQL text.
        where = " AND ".join(f"{key} = '{value}'" for key, value in filters)
        return self._call("db_query", {
            "db": database, "table": table, "select": ",".join(selected),
            "where": where, "limit": str(plan.limit), "orderby": f"{expected_key} asc",
        })


class AlisdDbQueryObserver:
    def __init__(self, *, client: AlisdDbQueryClient, database: str, environment: str,
                 environment_binding_confirmed: bool,
                 physical_binding_probe: Callable[[str, str], dict[str, Any]] | None = None):
        self.client = client
        self.database = identifier(database, "database")
        self.environment = identifier(environment, "environment")
        self.environment_binding_confirmed = environment_binding_confirmed
        self.physical_binding_probe = physical_binding_probe
        self._discovery: dict | None = None

    def _verify_physical_binding(self) -> None:
        self._binding_scope = {}
        if self.environment_binding_confirmed is not True:
            raise EvidenceError("database environment binding is not confirmed")
        # A profile boolean is only an operator declaration. For non-local
        # targets, a separately injected probe must prove the physical
        # database/environment mapping before metadata or row queries.
        if self.environment == "local":
            return
        if self.physical_binding_probe is None:
            raise EvidenceError("database physical environment probe is unavailable")
        try:
            result = self.physical_binding_probe(self.database, self.environment)
        except Exception as error:
            cause = primary_error(error)
            if isinstance(cause, KeyboardInterrupt):
                raise cause from None
            raise EvidenceError("database physical environment probe failed") from None
        if (not isinstance(result, dict) or result.get("status") != "BOUND"
                or result.get("database") != self.database
                or result.get("environment") != self.environment):
            raise EvidenceError("database physical environment binding mismatch")
        self._binding_scope = result

    def discover(self) -> dict:
        self._discovery = None
        self._verify_physical_binding()
        databases = self.client.db_list()
        if (not isinstance(databases, dict) or databases.get("success") is not True
                or type(databases.get("truncated", False)) is not bool or databases.get("truncated", False)):
            raise EvidenceError("database list discovery failed")
        values = databases.get("data")
        if (not isinstance(values, list) or not all(isinstance(value, str) for value in values)
                or self.database not in values):
            raise EvidenceError("configured database is not present in the bound environment")
        tables = self.client.table_list(self.database)
        if (not isinstance(tables, dict) or tables.get("success") is not True
                or type(tables.get("truncated", False)) is not bool or tables.get("truncated", False)):
            raise EvidenceError("table list discovery failed")
        table_values = tables.get("data")
        if not isinstance(table_values, list) or not all(isinstance(value, str) for value in table_values):
            raise EvidenceError("table list discovery returned invalid data")
        required_tables = sorted({value[0] for value in ASSERTIONS.values()})
        missing = [table for table in required_tables if table not in table_values]
        if missing:
            raise EvidenceError("required semantic evidence tables are missing")
        self._discovery = {"database": self.database, "environment": self.environment,
                           "tables": table_values, "required_tables": required_tables}
        return {"status": "DISCOVERY_PASSED", "source": "alisddbquery",
                "database": self.database, "environment": self.environment,
                "required_tables": required_tables}

    def provider(self) -> AlisdDbEvidenceProvider:
        if self._discovery is None:
            self.discover()
        return AlisdDbEvidenceProvider(
            database=self.database,
            environment=self.environment,
            environment_binding_confirmed=True,
            describe=self.client.table_describe,
            query=self.client.db_query,
        )

    # Expose the stable semantic-provider protocol to the adapter composition
    # root while retaining the discovery wrapper as the physical binding gate.
    def admit(self, assertion_ids: tuple[str, ...]) -> dict[str, Any]:
        provider = self.provider()
        scope = self._binding_scope
        if scope.get("verification_mode") == "OWNER_CONFIRMED_SHARED_DATABASE":
            allowed = scope.get("allowed_assertions")
            if (not isinstance(allowed, list) or not assertion_ids
                    or any(assertion not in allowed for assertion in assertion_ids)):
                raise EvidenceError("required Case observations are outside owner-confirmed scope")
        return provider.admit(assertion_ids)

    def observe(self, assertion_id: str, resource_id: str) -> dict[str, Any]:
        return self.provider().observe(assertion_id, resource_id)
