"""Run/Case-local adapter guard for the three reviewed pilot Stories.

This is an accidental-misuse guard, not a sandbox or server authorization.
The composition root supplies the already-bound ledger and frozen Case. No
resource is adopted from names/list results, and no ledger state is changed.
Recovery cleanup intentionally retains its independent ownership verifier.
"""
from __future__ import annotations

from .ledger import Ledger
from .termination import TerminationError, TerminationStateMachine


class CaseScopeError(RuntimeError):
    pass


class CaseScopedAdapter:
    def __init__(self, adapter, ledger: Ledger, profile_digest: str, case: dict, *, attempt: int,
                 termination_timeout_seconds: int = 30):
        self._adapter = adapter
        self._ledger = ledger
        self._digest = profile_digest
        self._case_id = case['id']
        self._attempt = attempt
        self._termination_timeout = termination_timeout_seconds
        self._effects = frozenset(case['effects'])
        self._initial_sessions: set[str] = set()
        self._submitted_intents: set[str] = set()
        self._deleted: set[str] = set()
        self._session_tokens: dict[str, str] = {}
        self._share_tokens: dict[str, tuple[str, str]] = {}
        self._connections: set[str] = set()
        self._terminations: dict[str, TerminationStateMachine] = {}

    def _effect(self, effect: str) -> None:
        self._ledger._check_binding(self._ledger.run_id, self._digest)
        if effect not in self._effects:
            raise CaseScopeError('operation effect is not declared by the frozen Case')

    def _live_chain(self, row, kind: str, *, verification: bool = False) -> tuple[dict, ...]:
        if (row['kind'] != kind or row['case_id'] != self._case_id
                or row['attempt'] != self._attempt or row['origin'] != 'created'
                or row['state'] != 'live' or not row['resource_id']
                or (not verification and row['handle'] in self._deleted)):
            raise CaseScopeError('resource is not live and created by this Case attempt')
        if kind == 'group':
            if row['parent'] is not None:
                raise CaseScopeError('group has an unexpected parent')
            return (dict(row),)
        else:
            # Fixed file -> session -> group shape bounds traversal to three rows;
            # cycles, wrong kinds and cross-Case parents fail, never loop.
            return (dict(row), *self._live_chain(self._ledger._get(row['parent']),
                                                'session' if kind == 'file' else 'group'))

    def _resource_chain(self, kind: str, resource_id: str, *, verification: bool = False) -> tuple[dict, ...]:
        self._ledger._check_binding(self._ledger.run_id, self._digest)
        row = self._ledger.db.execute(
            'SELECT * FROM resource WHERE kind=? AND resource_id=?', (kind, resource_id)).fetchone()
        if row is None:
            raise CaseScopeError('exact resource is not registered in this Run')
        return self._live_chain(row, kind, verification=verification)

    def _resource(self, kind: str, resource_id: str, *, verification: bool = False):
        return self._resource_chain(kind, resource_id, verification=verification)[0]

    def observation_scope(self, assertion_id: str, resource_id: str) -> tuple[dict, ...]:
        kinds = {"group.persisted": "group", "session.persisted": "session", "file.persisted": "file"}
        if assertion_id not in kinds:
            raise CaseScopeError("unreviewed resource observation")
        return self._resource_chain(kinds[assertion_id], resource_id)

    def _intent(self, kind: str, parent: str | None):
        rows = self._ledger.db.execute(
            'SELECT * FROM resource WHERE case_id=? AND attempt=? AND kind=? AND parent IS ? '
            "AND state='intended' AND origin='unknown' AND resource_id IS NULL LIMIT 2",
            (self._case_id, self._attempt, kind, parent)).fetchall()
        if len(rows) != 1 or rows[0]['handle'] in self._submitted_intents:
            raise CaseScopeError('one fresh unsubmitted creation intent is required')
        return rows[0]

    def _termination(self, session_id: str, resource_handle: str) -> TerminationStateMachine:
        existing = self._terminations.get(session_id)
        if existing is not None:
            return existing

        def persist(snapshot: dict) -> None:
            self._ledger.save_termination(
                resource_handle=resource_handle, case_id=self._case_id, attempt=self._attempt,
                session_id=session_id, phase=snapshot["phase"],
                reason=(snapshot["history"][-1].get("reason") if snapshot.get("history") else None),
                history=snapshot["history"],
            )

        saved = self._ledger.termination_state(resource_handle)
        machine = (TerminationStateMachine.from_snapshot(saved, persist=persist)
                   if saved is not None else TerminationStateMachine(
                       session_id, persist=persist, timeout_seconds=self._termination_timeout))
        if saved is None:
            persist(machine.snapshot())
        self._terminations[session_id] = machine
        return machine

    def _assert_session_actions_open(self, session_id: str) -> None:
        machine = self._terminations.get(session_id)
        if machine is not None and machine.phase != "OPEN":
            raise CaseScopeError("Session termination has stopped new actions")

    def termination_snapshots(self) -> list[dict]:
        return [machine.snapshot() for machine in self._terminations.values()]

    def _quiesce_session(self, session_id: str, resource_handle: str) -> TerminationStateMachine:
        machine = self._termination(session_id, resource_handle)
        try:
            if machine.phase == "QUIESCENT":
                machine.remaining_seconds()
                return machine
            mode = getattr(self._adapter, "termination_mode", None)
            if mode not in {"LOCAL_CONTROLLED", "REVIEWED_REMOTE"}:
                machine.retain("adapter termination capability is not admitted")
                raise CaseScopeError("exact Session termination capability is not admitted")
            if mode == "REVIEWED_REMOTE" and not all(callable(getattr(self._adapter, name, None))
                    for name in ("close_production_channels", "quiesce_execution", "close_termination_channel")):
                raise CaseScopeError("remote termination callbacks are not complete")
            machine.stop_new_actions()
            close_production = getattr(self._adapter, "close_production_channels", None)
            machine.close_production(
                (lambda: close_production(session_id)) if callable(close_production) else None
            )
            quiesce = getattr(self._adapter, "quiesce_execution", None)
            machine.await_terminal(
                (lambda: quiesce(session_id)) if callable(quiesce) else None
            )
            return machine
        except (TerminationError, CaseScopeError) as error:
            if machine.phase not in {"CLEANED", "RETAINED"}:
                machine.retain(f"termination failed: {type(error).__name__}")
            raise
        except Exception as error:
            machine.retain(f"termination failed: {type(error).__name__}")
            raise CaseScopeError("Session termination could not be confirmed") from None

    def session_quiesce(self, session_id: str) -> None:
        """Fence producers and settle effects while retaining the owned WS."""
        row = self._session_deletion(session_id)
        self._quiesce_session(session_id, row['handle'])

    def reconcile_session_cleanup_ready(self, session_id: str) -> dict:
        """Persist the exact owned Session termination fence for recovery.

        This is not a global drain or a new stop API. It re-runs the same finite
        reviewed Session lifecycle boundary against the already-created ledger
        row, then exposes the resulting machine snapshot for explicit recovery.
        """
        row = self._session_deletion(session_id)
        self._terminate_session(session_id, row['handle'])
        machine = self._termination(session_id, row['handle'])
        if machine.phase != "CLEANUP_READY":
            raise CaseScopeError("reconciled Session did not reach cleanup readiness")
        return machine.snapshot()

    def _terminate_session(self, session_id: str, resource_handle: str) -> None:
        machine = self._quiesce_session(session_id, resource_handle)
        try:
            close_control = getattr(self._adapter, "close_termination_channel", None)
            machine.close_control(
                (lambda: close_control(session_id)) if callable(close_control) else None
            )
            machine.ready_for_cleanup()
        except Exception:
            machine.retain("control channel closure was not confirmed")
            raise

    def _delete(self, row, operation, *args):
        if self._ledger.db.execute(
                "SELECT 1 FROM resource WHERE parent=? AND state!='deleted' LIMIT 1",
                (row['handle'],)).fetchone():
            raise CaseScopeError('children must be reconciled before a parent API deletion')
        if row['kind'] == 'session' and row['resource_id'] in self._connections:
            raise CaseScopeError('owned WebSocket must be closed before Session deletion')
        if row['kind'] == 'session':
            self._terminate_session(row['resource_id'], row['handle'])
        result = operation(*args)
        data, _ = result
        if isinstance(data, dict) and data.get('deleted') is True:
            # Keep the ledger live until the Story verifies API/share absence.
            self._deleted.add(row['handle'])
        return result

    def preflight(self):
        return self._adapter.preflight()

    def semantic_observer(self):
        # Composition also supplies observation_scope to the admitted observer;
        # provider metadata/physical-environment admission stays independent.
        return self._adapter.semantic_observer()

    def group_create(self, body: dict):
        self._effect('create:group')
        self._effect('create:initial_session')
        if body.get('group_kind') != 'normal':
            raise CaseScopeError('only normal Group creation is reviewed for this pilot')
        group = self._intent('group', None)
        session = self._intent('session', group['handle'])
        self._submitted_intents.update((group['handle'], session['handle']))
        self._initial_sessions.add(session['handle'])
        return self._adapter.group_create(body)

    def group_read(self, group_id: str):
        self._resource('group', group_id)
        return self._adapter.group_read(group_id)

    def group_update(self, group_id: str, body: dict):
        self._effect('update:owned_group')
        self._resource('group', group_id)
        return self._adapter.group_update(group_id, body)

    def group_delete(self, group_id: str):
        self._effect('delete:owned_group')
        return self._delete(self._resource('group', group_id), self._adapter.group_delete, group_id)

    def group_verify_deleted(self, group_id: str):
        self._effect('delete:owned_group')
        self._resource('group', group_id, verification=True)
        return self._adapter.group_verify_deleted(group_id)

    def session_create(self, group_id: str, body: dict):
        self._effect('create:session')
        group = self._resource('group', group_id)
        session = self._intent('session', group['handle'])
        self._submitted_intents.add(session['handle'])
        return self._adapter.session_create(group_id, body)

    def session_read(self, session_id: str):
        self._assert_session_actions_open(session_id)
        self._resource('session', session_id)
        return self._adapter.session_read(session_id)

    def session_update(self, session_id: str, body: dict):
        self._assert_session_actions_open(session_id)
        self._effect('update:owned_session')
        self._resource('session', session_id)
        return self._adapter.session_update(session_id, body)

    def session_messages(self, session_id: str):
        self._assert_session_actions_open(session_id)
        self._resource('session', session_id)
        return self._adapter.session_messages(session_id)

    def session_token(self, session_id: str):
        self._assert_session_actions_open(session_id)
        self._effect('mint:session_token')
        self._resource('session', session_id)
        result = self._adapter.session_token(session_id)
        data, _ = result
        if isinstance(data, dict):
            token = data.get('token') or data.get('connection_token')
            if isinstance(token, str) and token:
                self._session_tokens[token] = session_id
        return result

    def session_ws_connect(self, group_id: str, session_id: str, token: str):
        self._assert_session_actions_open(session_id)
        self._effect('mint:session_token')
        session = self._resource('session', session_id)
        group = self._resource('group', group_id)
        if (session['parent'] != group['handle'] or self._session_tokens.get(token) != session_id
                or session_id in self._connections):
            raise CaseScopeError('WebSocket target/token is not bound to this owned Session')
        result = self._adapter.session_ws_connect(group_id, session_id, token)
        self._connections.add(session_id)
        return result

    def session_ws_close(self, session_id: str):
        # Release only a socket obtained by this wrapper; no fresh DB/server/auth
        # check may prevent closing it after server death or ledger failure.
        if session_id not in self._connections:
            raise CaseScopeError('no WebSocket held by this Case for that Session')
        result = self._adapter.session_ws_close(session_id)
        self._connections.remove(session_id)
        return result

    def _session_deletion(self, session_id: str, *, verification: bool = False):
        row = self._resource('session', session_id, verification=verification)
        self._effect('delete:owned_initial_session' if row['handle'] in self._initial_sessions
                     else 'delete:owned_session')
        return row

    def session_delete(self, session_id: str):
        row = self._session_deletion(session_id)
        return self._delete(row, self._adapter.session_delete, session_id)

    def session_verify_deleted(self, session_id: str):
        self._session_deletion(session_id, verification=True)
        result = self._adapter.session_verify_deleted(session_id)
        machine = self._terminations.get(session_id)
        if machine is not None and machine.phase == "CLEANUP_READY":
            machine.cleanup_complete()
        return result

    def file_list(self, session_id: str):
        self._assert_session_actions_open(session_id)
        self._resource('session', session_id)
        return self._adapter.file_list(session_id)

    def file_prepare(self, session_id: str, body: dict):
        self._assert_session_actions_open(session_id)
        self._effect('upload:file')
        session = self._resource('session', session_id)
        row = self._intent('file', session['handle'])
        self._submitted_intents.add(row['handle'])
        return self._adapter.file_prepare(session_id, body)

    def _file(self, session_id: str, file_id: str, *, verification: bool = False):
        row = self._resource('file', file_id, verification=verification)
        if self._ledger._get(row['parent'])['resource_id'] != session_id:
            raise CaseScopeError('File target differs from its registered Session parent')
        return row

    def file_upload(self, session_id: str, file_id: str, content: bytes):
        self._assert_session_actions_open(session_id)
        self._effect('upload:file')
        self._file(session_id, file_id)
        return self._adapter.file_upload(session_id, file_id, content)

    def file_complete(self, session_id: str, file_id: str):
        self._assert_session_actions_open(session_id)
        self._effect('upload:file')
        self._file(session_id, file_id)
        return self._adapter.file_complete(session_id, file_id)

    def file_metadata(self, session_id: str, file_id: str):
        self._assert_session_actions_open(session_id)
        self._file(session_id, file_id)
        return self._adapter.file_metadata(session_id, file_id)

    def file_download(self, session_id: str, file_id: str):
        self._assert_session_actions_open(session_id)
        self._file(session_id, file_id)
        return self._adapter.file_download(session_id, file_id)

    def file_share(self, session_id: str, file_id: str):
        self._assert_session_actions_open(session_id)
        self._effect('share:owned_file')
        self._file(session_id, file_id)
        result = self._adapter.file_share(session_id, file_id)
        data, _ = result
        if isinstance(data, dict) and isinstance(data.get('share_token'), str) and data['share_token']:
            self._share_tokens[data['share_token']] = (session_id, file_id)
        return result

    def _shared_file(self, token: str, *, verification: bool = False):
        self._effect('share:owned_file')
        target = self._share_tokens.get(token)
        if target is None:
            raise CaseScopeError('share token was not issued for this Case')
        self._file(*target, verification=verification)

    def shared_file_download(self, token: str):
        target = self._share_tokens.get(token)
        if target is None:
            raise CaseScopeError('share token was not issued for this Case')
        self._assert_session_actions_open(target[0])
        self._shared_file(token)
        return self._adapter.shared_file_download(token)

    def file_delete(self, session_id: str, file_id: str):
        self._assert_session_actions_open(session_id)
        self._effect('delete:owned_file')
        return self._delete(self._file(session_id, file_id), self._adapter.file_delete, session_id, file_id)

    def file_verify_deleted(self, session_id: str, file_id: str):
        self._effect('delete:owned_file')
        self._file(session_id, file_id, verification=True)
        return self._adapter.file_verify_deleted(session_id, file_id)

    def shared_file_verify_deleted(self, token: str):
        self._effect('delete:owned_file')
        self._shared_file(token, verification=True)
        return self._adapter.shared_file_verify_deleted(token)
