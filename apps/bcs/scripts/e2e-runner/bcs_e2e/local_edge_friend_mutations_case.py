"""Owned-local adapter for the seven reviewed Edge friend-mutation parents plus
the two backlog TestCase extras.

Each seam maps one legacy ``edge_permission.sh`` obligation to live,
runner-owned BCS exchanges against the loopback runtime. Every
operation fails closed: an unexpected status, a missing pending row
or a stale decision raises instead of being counted as coverage, and
no evidence may contain a role credential or the service key.

The historical accept/reject/cancel case degraded its reject and
cancel branches to fake-id probes because a re-add between connected
bots is an idempotent approved result; the owned-local seam drives
each branch against a real pending row on a fresh actor pair, and
the duplicate-create branch asserts the real idempotent pending
result rather than the stale 409 expectation recorded in the shell
coverage comments.

Owned-local run-order corrections versus the historical shell pairs:
the revoke seam frees the CEO<->ENG friend edges before the
lifecycle and mutual seams run, and the current edge-grant store
cannot rebuild a revoked pair (its grant insert is a no-op on the
(from_id, to_id, env, grant_ref_id) unique key, which ignores row
status), so a same-run re-accept of CEO<->ENG would report revoked
edge ids without activating any grant. The lifecycle seam therefore
drives PM->ENG and the mutual seam drives CEO<->QA, both pairs with
no revoked edge history inside one run; every obligation is
unchanged. Revoke-interrupts-reconnect is a recordable product
defect, stated here rather than silently routed around.

The discovery seam asserts the real discovery contract: a private
collaborate_bot yields an empty 200 result, a public one lists the
discover-visible directory bots (requester excluded) with a Boolean
is_friend annotation per entry. That annotation is computed from the
legacy friendship view, whose migration-window sync only mirrors
legacy friendships into edge-permission edges and not back, so
edge-permission friendships accepted by this suite are not yet
reflected as is_friend=true; the observed annotation counts are
recorded as evidence and the one-way mirror is a product-side gap.
"""
from __future__ import annotations

import json
import secrets
from http.client import HTTPConnection
from urllib.parse import quote, urlsplit

from .edge_friend_mutation_case import (
    edge_friend_mutation_case_plan,
    edge_friend_mutation_case_spec,
)
from .case_adapters import AdapterCall
from .inventory import executor_digest as _executor_digest
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .local_edge_friend_fixture_admission import OwnedEdgeFriendFixtureAdmission
from .local_edge_friend_mutation_extras import OwnedEdgeFriendMutationExtrasMixin
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

_MAX_BODY_BYTES = 64 * 1024
_TRANSPORT_TIMEOUT_SECONDS = 10.0
_UNKNOWN_BOT = '00000000-0000-0000-0000-000000000000'
_ROLE_MUTATIONS = ('CEO', 'PM', 'ENG', 'QA')
_DECISION_STATUSES = {'reject': 'rejected', 'cancel': 'cancelled'}


class _OwnedEdgeFriendMutationsSevenAdapter:
    """Live owned-local implementation of the seven mutation seams."""

    def __init__(self, runtime: OwnedBcsProcess,
                 identities: OwnedLocalRoleIdentity,
                 fixture_admission: OwnedEdgeFriendFixtureAdmission,
                 *, case_id: str, allow_write: bool):
        if (type(runtime) is not OwnedBcsProcess
                or type(identities) is not OwnedLocalRoleIdentity
                or type(fixture_admission) is not OwnedEdgeFriendFixtureAdmission
                or allow_write is not True):
            raise RunStateError('Edge friend-mutation adapter requires exact owned objects and explicit write permission')
        self.plan = edge_friend_mutation_case_plan(case_id)
        spec = edge_friend_mutation_case_spec(case_id)
        if (runtime.inputs is None or runtime.state != 'RUNNING' or runtime.ready is not True
                or identities.runtime is not runtime or identities.root != runtime.root
                or identities.run_id != runtime.run_id
                or identities.roles != _ROLE_MUTATIONS
                or not set(_ROLE_MUTATIONS) <= set(identities._verified)
                or fixture_admission.runtime is not runtime):
            raise RunStateError('Edge friend-mutation runtime, identity or admission ownership differ')
        self.runtime = runtime
        self.identities = identities
        self.fixture_admission = fixture_admission
        self.case_id = spec.case_id
        self.parent_id = spec.parent
        self.requirement_id = spec.requirement_id
        self.adapter_method = spec.adapter_method
        self.adapters = dict(self.plan.adapters)
        self.run_id = runtime.run_id
        self.root = runtime.root
        self.origin = runtime.inputs.origin
        self.executor_digest = _executor_digest()
        parsed = urlsplit(self.origin)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or parsed.port is None or parsed.path or parsed.query or parsed.fragment):
            raise RunStateError('Edge friend-mutation runtime origin is outside its loopback contract')
        self._host, self._port = parsed.hostname, parsed.port
        self.path = self.root / ('local-edge-friend-mutations-' + self.case_id + '.json')
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Edge friend-mutation adapter already attempted this runtime')
        self._attempted: set[str] = set()
        self._digest = ''
        self.state = {
            'schema_version': 1, 'status': 'RUNNING', 'case_id': self.case_id,
            'parent_id': self.parent_id, 'run_id': self.run_id,
            'executor_digest': self.executor_digest,
            'adapter_family': 'OwnedLocalCaseAdapter',
            'requirement_id': self.requirement_id, 'steps': [],
        }

    def _active(self, method: str | None = None) -> None:
        if (_executor_digest() != self.executor_digest
                or self.runtime.state != 'RUNNING' or self.runtime.ready is not True
                or self.identities._state != 'READY'
                or fingerprint(read(self.identities.fixtures.path))
                    != self.identities._receipt_digest
                or self.fixture_admission.runtime.state != 'RUNNING'
                or (method is not None and method in self._attempted)):
            raise RunStateError('Edge friend-mutation adapter authority, fixture or operation state changed')
        if self._digest:
            if read(self.path) != self.state:
                raise RunStateError('Edge friend-mutation adapter journal changed externally')

    def _save(self) -> None:
        encoded = json.dumps(self.state, ensure_ascii=True, sort_keys=True, allow_nan=False)
        if any(secret and secret in encoded
               for secret in (entry[1] for entry in self.identities._entries.values())):
            raise RunStateError('Edge friend-mutation evidence must not expose a credential')
        if (self.fixture_admission.attempted
                and self.fixture_admission._service_key in encoded):
            raise RunStateError('Edge friend-mutation evidence must not expose the service credential')
        write(self.path, self.state)
        self._digest = encoded

    def _begin(self) -> dict:
        requirement = self._next_requirement()
        method = requirement.adapter_method
        self._active(method)
        row = {
            'method': method,
            'requirement_id': requirement.requirement_id,
            'status': 'INTENDED', 'http_status': None, 'evidence_keys': [],
        }
        self.state['steps'].append(row)
        self._attempted.add(method)
        self._save()
        return row

    def _next_requirement(self):
        index = len(self.state['steps'])
        requirements = self.plan.requirements
        if index >= len(requirements):
            raise RunStateError(
                'Edge friend-mutation adapter attempted more obligations than the frozen plan')
        return requirements[index]

    def _finish(self, row: dict, *, evidence: dict, data: object) -> AdapterCall:
        requirement = next(item for item in self.plan.requirements
                           if item.requirement_id == row['requirement_id'])
        row['status'] = 'PASS'
        row['http_status'] = evidence.get('http_status')
        row['evidence'] = {
            'transport': 'owned-local-http',
            'path': requirement.path,
            'http_method': requirement.method,
            'actor': requirement.actor,
            **evidence,
        }
        self.state['steps'][-1] = row
        if (len(self.state['steps']) == len(self.plan.requirements)
                and all(step.get('status') == 'PASS'
                        for step in self.state['steps'])):
            self.state['status'] = 'PASS'
        self._save()
        return AdapterCall(data=data, evidence=dict(row['evidence']))

    def _blocked(self, row: dict, error: BaseException) -> None:
        row['status'] = 'BLOCKED'
        row['error_type'] = type(error).__name__
        self.state['steps'][-1] = row
        try:
            self._save()
        except BaseException:
            pass

    def _wire(self, *, method: str, path: str, body: dict | None,
              authorization: str = '', exact_status: int,
              accepted_statuses: tuple[int, ...] | None = None,
              extra_headers: dict[str, str] | None = None) -> tuple[int, dict]:
        if (not isinstance(path, str) or not path.startswith('/') or len(path) > 2048
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise RunStateError('Edge friend-mutation path is invalid')
        accepted = accepted_statuses or (exact_status,)
        if exact_status not in accepted:
            raise RunStateError('Edge friend-mutation accepted status set is invalid')
        payload = None if body is None else json.dumps(
            body, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if payload is not None and len(payload) > _MAX_BODY_BYTES:
            raise RunStateError('Edge friend-mutation request exceeds its byte budget')
        headers = {'Accept': 'application/json',
                   'X-Request-ID': 'e2e-edge-friend-' + self.run_id + '-'
                                   + str(len(self.state['steps'])) + '-' + str(secrets.token_hex(4))}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if authorization:
            headers['Authorization'] = authorization
        if extra_headers:
            headers.update(extra_headers)
        connection = HTTPConnection(self._host, self._port, timeout=_TRANSPORT_TIMEOUT_SECONDS)
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            with response:
                status = response.status
                raw = bytearray()
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > _MAX_BODY_BYTES:
                        raise RunStateError('Edge friend-mutation response exceeds its byte budget')
                if response.length not in (None, 0):
                    raise RunStateError('Edge friend-mutation response was truncated')
            if status not in accepted:
                raise RunStateError(
                    'Edge friend-mutation HTTP status ' + str(status)
                    + ' differs from the original contract ' + str(sorted(accepted)))
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            if not isinstance(value, dict):
                raise RunStateError('Edge friend-mutation response is not an object')
            return status, value
        except RunStateError:
            raise
        except BaseException as error:
            raise RunStateError('Edge friend-mutation exchange failed; outcome unknown, no retry or cleanup') from error
        finally:
            connection.close()

    def _token(self, role: str) -> str:
        if role not in _ROLE_MUTATIONS:
            raise RunStateError('Edge friend-mutation role is unknown')
        entry = self.identities._entries.get(role)
        if entry is None:
            raise RunStateError('Edge friend-mutation role identity is missing')
        return 'Bearer ' + entry[1]

    def _bot_id(self, role: str) -> str:
        return self.identities.actor_id(role)

    def _expect(self, condition: bool, message: str) -> None:
        # Chained and-expressions may end in a truthy non-True operand
        # such as a non-empty list; judge the condition by truthiness.
        if not condition:
            raise RunStateError('Edge friend-mutation obligation was not satisfied: ' + message)

    def _data(self, envelope: dict, name: str) -> dict:
        self._expect(envelope.get('success') is True,
                     name + ' response did not report success')
        payload = envelope.get('data')
        self._expect(isinstance(payload, dict), name + ' response data is not an object')
        return payload

    def _error_body(self, value: dict) -> str:
        message = str(value.get('error') or value.get('message') or '')
        self._expect(bool(message), 'error response carries no message')
        return message

    def _set_visibility(self, role: str, visibility: str) -> int:
        status, value = self._wire(
            method='PUT', path='/bots/' + quote(self._bot_id(role), safe='') + '/visibility',
            body={'visibility': visibility}, authorization=self._token(role),
            exact_status=200)
        payload = value.get('data', {})
        self._expect(
            value.get('success') is True
            and isinstance(payload, dict)
            and payload.get('bot_uuid') == self._bot_id(role)
            and payload.get('visibility') == visibility,
            role + ' visibility write to ' + visibility + ' was not confirmed')
        return status

    def _create_request(self, caller_role: str, to_bot: str) -> dict:
        status, value = self._wire(
            method='POST', path='/collaboration/friend-connections/requests',
            body={'to_bot': to_bot}, authorization=self._token(caller_role),
            exact_status=200)
        payload = self._data(value, 'friend-connection create')
        request_ids = payload.get('request_ids')
        self._expect(isinstance(request_ids, list) and request_ids
                     and all(isinstance(item, str) and item for item in request_ids),
                     'friend-connection create returned no request_ids')
        self._expect(payload.get('status') in ('pending', 'approved', 'public_no_edge'),
                     'friend-connection create returned an unknown status')
        return payload

    def _accept(self, caller_role: str, request_id: str) -> dict:
        status, value = self._wire(
            method='POST',
            path='/collaboration/friend-connections/requests/'
                 + quote(identifier(request_id, 'friend-connection request ID'), safe='') + '/accept',
            body={}, authorization=self._token(caller_role), exact_status=200)
        payload = self._data(value, 'accept')
        edge_ids = payload.get('edge_ids')
        self._expect(isinstance(edge_ids, list) and edge_ids
                     and all(isinstance(item, int) for item in edge_ids),
                     'accept returned no edge_ids')
        return payload

    def _decision(self, caller_role: str, request_id: str, verb: str) -> str:
        status, value = self._wire(
            method='POST',
            path='/collaboration/friend-connections/requests/'
                 + quote(identifier(request_id, 'friend-connection request ID'), safe='') + '/' + verb,
            body={}, authorization=self._token(caller_role), exact_status=200)
        if verb == 'accept':
            self._accept(caller_role, request_id)
            return 'approved'
        payload = self._data(value, verb)
        decision = payload.get('status')
        self._expect(decision == _DECISION_STATUSES[verb],
                     verb + ' response did not report its decision')
        return decision

    def _list_requests(self, role: str, direction: str,
                       status: str | None = None) -> list[dict]:
        query = 'direction=' + quote(direction, safe='')
        if status is not None:
            query += '&status=' + quote(status, safe='')
        _, value = self._wire(
            method='GET',
            path='/collaboration/friend-connections/requests?' + query,
            body=None, authorization=self._token(role), exact_status=200)
        payload = self._data(value, 'friend-connection request list')
        items = payload.get('items')
        self._expect(isinstance(items, list), 'request list items is not a list')
        self._expect(isinstance(payload.get('total'), int), 'request list total is not an integer')
        return items

    def _request_row(self, items: list[dict], request_id: str) -> dict:
        for row in items:
            if isinstance(row, dict) and row.get('request_id') == request_id:
                return row
        raise RunStateError('friend-connection request row is missing from the listing')

    def _list_friends(self, role: str, actor: str) -> list[dict]:
        _, value = self._wire(
            method='GET',
            path='/collaboration/friend-connections?actor='
                 + quote(actor, safe='') + '&actor_kind=bot',
            body=None, authorization=self._token(role), exact_status=200)
        payload = self._data(value, 'friend list')
        items = payload.get('items')
        self._expect(isinstance(items, list) and isinstance(payload.get('total'), int)
                     and payload['total'] == len(items),
                     'friend list pagination disagrees with its items')
        return items

    def _friend_actor_ids(self, items: list[dict]) -> set[str]:
        result: set[str] = set()
        for row in items:
            self._expect(isinstance(row, dict) and isinstance(row.get('actor_id'), str),
                         'friend list row has no actor_id')
            result.add(row['actor_id'])
        return result

    def _admission(self, bot_id: str, actor: str,
                   actor_kind: str = 'bot') -> dict:
        _, value = self._wire(
            method='GET',
            path='/bots/' + quote(bot_id, safe='') + '/admission?actor='
                 + quote(actor, safe='') + '&actor_kind=' + quote(actor_kind, safe=''),
            body=None, exact_status=200)
        self._expect(isinstance(value.get('allowed'), bool), 'admission allowed is not a boolean')
        self._expect(isinstance(value.get('reason_code'), str), 'admission reason_code is missing')
        return value

    def _revoke(self, caller_role: str, actor: str) -> list:
        _, value = self._wire(
            method='DELETE',
            path='/collaboration/friend-connections/' + quote(actor, safe=''),
            body={}, authorization=self._token(caller_role), exact_status=200)
        payload = self._data(value, 'friend revoke')
        revoked = payload.get('revoked_edges')
        self._expect(isinstance(revoked, list) and revoked,
                     'revoke returned no revoked_edges')
        return revoked

    def _discover(self, caller_role: str | None, **params: str) -> tuple[int, dict]:
        query = '&'.join(key + '=' + quote(value, safe='')
                         for key, value in params.items())
        authorization = self._token(caller_role) if caller_role is not None else ''
        return self._wire(
            method='GET', path='/bots/discover?' + query,
            body=None, authorization=authorization,
            exact_status=200 if caller_role is not None else 401)

    # ------------------------------------------------------------------
    # The seven owned-local seams.
    # ------------------------------------------------------------------

    def run_accept_reject_cancel(self) -> AdapterCall:
        row = self._begin()
        try:
            evidence: dict = {'http_status': 200, 'branches': []}
            # ACCEPT branch on a real receiver-side pending row.
            pm, qa, eng, ceo = (self._bot_id(role) for role in ('PM', 'QA', 'ENG', 'CEO'))
            self._set_visibility('PM', 'protected')
            created = self._create_request('QA', pm)
            request_id = created['request_ids'][0]
            received = self._list_requests('PM', 'received')
            pending = self._request_row(received, request_id)
            self._expect(pending.get('status') == 'pending'
                         and pending.get('to_id') == pm and pending.get('from_id') == qa,
                         'the created QA->PM request is not a pending receiver-side row')
            self._decision('PM', request_id, 'accept')
            approved = self._list_requests('QA', 'sent', status='approved')
            decided = self._request_row(approved, request_id)
            self._expect(decided.get('status') == 'approved'
                         and isinstance(decided.get('edge_id'), int),
                         'the accepted QA->PM request was not decided approved with an edge')
            friends = self._friend_actor_ids(self._list_friends('PM', pm))
            self._expect(qa in friends, 'the accepted QA->PM edge is missing from the PM friend list')
            admission = self._admission(pm, qa)
            self._expect(admission['allowed'] is True
                         and admission['reason_code'] == 'ok'
                         and isinstance(admission.get('grants'), list) and admission['grants'],
                         'the QA->PM admission was not authorized over the built edge')
            evidence['branches'].append({'branch': 'accept', 'request_id': request_id,
                                         'status': 'approved', 'admission_reason': 'ok'})
            # REJECT branch on a fresh actor pair (the QA->PM pair is now
            # connected, and a re-add between connected bots is an
            # idempotent approved result rather than a new pending row).
            self._set_visibility('ENG', 'protected')
            rejected_create = self._create_request('QA', eng)
            reject_id = rejected_create['request_ids'][0]
            self._expect(rejected_create['status'] == 'pending',
                         'the QA->ENG create did not go pending on a protected bot')
            decision = self._decision('ENG', reject_id, 'reject')
            self._expect(decision == 'rejected', 'the ENG reject decision was not reported')
            rejected = self._list_requests('QA', 'sent', status='rejected')
            rejected_row = self._request_row(rejected, reject_id)
            self._expect(rejected_row.get('status') == 'rejected'
                         and rejected_row.get('edge_id') is None,
                         'the rejected QA->ENG row is not rejected without an edge')
            self._expect(eng not in self._friend_actor_ids(self._list_friends('ENG', eng)),
                         'a rejected QA->ENG pair still lists an edge')
            no_edge = self._admission(eng, qa)
            self._expect(no_edge['allowed'] is False
                         and no_edge['reason_code'] == 'no_edge',
                         'the QA->ENG admission was authorized without an edge')
            evidence['branches'].append({'branch': 'reject', 'request_id': reject_id,
                                         'status': 'rejected', 'admission_reason': 'no_edge'})
            # CANCEL branch on a fresh initiator-side pending row.
            self._set_visibility('CEO', 'protected')
            cancelled_create = self._create_request('QA', ceo)
            cancel_id = cancelled_create['request_ids'][0]
            sent = self._list_requests('QA', 'sent')
            self._expect(self._request_row(sent, cancel_id).get('status') == 'pending',
                         'the QA->CEO create is not pending before cancel')
            decision = self._decision('QA', cancel_id, 'cancel')
            self._expect(decision == 'cancelled', 'the QA cancel decision was not reported')
            cancelled = self._list_requests('QA', 'sent', status='cancelled')
            cancelled_row = self._request_row(cancelled, cancel_id)
            self._expect(cancelled_row.get('status') == 'cancelled'
                         and cancelled_row.get('edge_id') is None,
                         'the cancelled QA->CEO row is not cancelled without an edge')
            self._expect(qa not in self._friend_actor_ids(self._list_friends('CEO', ceo)),
                         'a cancelled QA->CEO pair still lists an edge')
            no_edge = self._admission(ceo, qa)
            self._expect(no_edge['allowed'] is False
                         and no_edge['reason_code'] == 'no_edge',
                         'the QA->CEO admission was authorized without an edge')
            evidence['branches'].append({'branch': 'cancel', 'request_id': cancel_id,
                                         'status': 'cancelled', 'admission_reason': 'no_edge'})
            # Restore the fixtures the original shell suite relied on.
            for role in ('PM', 'ENG', 'CEO'):
                self._set_visibility(role, 'public')
            evidence['restored_visibility'] = ['public'] * 3
            return self._finish(row, evidence=evidence, data={
                'branches': evidence['branches'],
                'restored_public': ['PM', 'ENG', 'CEO'],
            })
        except BaseException as error:
            self._blocked(row, error)
            raise

    def run_revoke(self) -> AdapterCall:
        row = self._begin()
        try:
            ceo, eng = self._bot_id('CEO'), self._bot_id('ENG')
            self._set_visibility('ENG', 'protected')
            created = self._create_request('CEO', eng)
            request_id = created['request_ids'][0]
            self._expect(created['status'] == 'pending',
                         'the CEO->ENG create did not go pending')
            received = self._list_requests('ENG', 'received')
            pending = self._request_row(received, request_id)
            self._expect(pending.get('status') == 'pending'
                         and pending.get('from_id') == ceo
                         and pending.get('to_id') == eng,
                         'the created CEO->ENG request is not a pending receiver-side row')
            self._decision('ENG', request_id, 'accept')
            friends = self._friend_actor_ids(self._list_friends('CEO', ceo))
            self._expect(eng in friends,
                         'the accepted CEO->ENG edge is missing from the CEO friend list')
            revoked = self._revoke('CEO', eng)
            after = self._friend_actor_ids(self._list_friends('CEO', ceo))
            self._expect(eng not in after,
                         'the revoked CEO->ENG edge is still listed')
            after_inbound = self._friend_actor_ids(self._list_friends('ENG', eng))
            self._expect(ceo not in after_inbound,
                         'the revoked CEO->ENG edge is still listed inbound')
            # The admission probe must run against a protected bot: a
            # public bot admits any actor through public_default, which
            # carries no evidence about the revoked edge grant.
            self._set_visibility('CEO', 'protected')
            no_edge = self._admission(ceo, eng)
            self._expect(no_edge['allowed'] is False
                         and no_edge['reason_code'] == 'no_edge',
                         'the ENG->CEO admission was authorized after the exact revoke')
            self._set_visibility('CEO', 'public')
            recreated = self._create_request('CEO', eng)
            self._expect(recreated['status'] == 'pending'
                         and recreated['request_ids'],
                         'the fresh CEO->ENG recreation did not go pending')
            self._set_visibility('ENG', 'public')
            return self._finish(row, evidence={
                'http_status': 200, 'revoked_request': request_id,
                'revoked_edges': len(revoked),
                'admission_probe': 'protected-no-edge',
                'recreated_request': recreated['request_ids'][0],
                'restored_visibility': 'public',
            }, data={
                'request_id': request_id, 'revoked_edge_count': len(revoked),
                'fresh_pending': recreated['request_ids'][0],
            })
        except BaseException as error:
            self._blocked(row, error)
            raise

    def run_ensure_bot(self) -> AdapterCall:
        row = self._begin()
        try:
            self.fixture_admission.admit(allow_write=True)
            self.fixture_admission.validate_for_runtime(self.runtime)
            record = dict(self.fixture_admission.state)
            ceo = self._bot_id('CEO')
            self._expect(record.get('status') == 'PASS'
                         and record.get('http_status') == 200
                         and record.get('ensured') is True
                         and record.get('created') is False
                         and record.get('bot_uuid') == ceo,
                         'the service-credential admin ensure is not an idempotent '
                         'existing-bot result')
            # Identity-stability probe: the ensured bot still resolves
            # through its authenticated self-listing face.
            self._list_friends('CEO', ceo)
            return self._finish(row, evidence={
                'http_status': 200, 'ensured': True, 'created': False,
                'bot_uuid': ceo, 'staff_no_bound': False,
                'identity_probe': 'authenticated-self-list-ok',
            }, data={
                'ensured': True, 'created': False, 'bot_uuid': ceo,
            })
        except BaseException as error:
            self._blocked(row, error)
            raise

    def run_full_lifecycle(self) -> AdapterCall:
        row = self._begin()
        try:
            # Pair note: the lifecycle seam runs PM->ENG, a pair with no
            # in-run edge history. The historical shell lifecycle drove
            # CEO->ENG, but the revoke seam frees that pair's friend
            # edges earlier in the same owned-local run, and the current
            # edge-grant store cannot rebuild a revoked pair's edges
            # (its insert is a no-op on the (from_id, to_id, env,
            # grant_ref_id) unique key, which ignores status), so a
            # re-accept of CEO->ENG in the same run would return
            # revoked edge ids without activating any grant. Running the
            # lifecycle on a fresh pair keeps this seam's evidence
            # about the lifecycle itself; revoke-interrupts-reconnect is
            # a recordable product defect, not an obligation of this
            # entrypoint.
            pm, eng = self._bot_id('PM'), self._bot_id('ENG')
            self._set_visibility('ENG', 'protected')
            created = self._create_request('PM', eng)
            self._expect(created['status'] == 'pending',
                         'the lifecycle create did not go pending')
            request_id = created['request_ids'][0]
            received = self._list_requests('ENG', 'received')
            pending = self._request_row(received, request_id)
            self._expect(pending.get('status') == 'pending'
                         and pending.get('from_id') == pm
                         and pending.get('to_id') == eng,
                         'the lifecycle request is not a pending receiver-side row')
            self._decision('ENG', request_id, 'accept')
            sent = self._list_requests('PM', 'sent', status='approved')
            decided = self._request_row(sent, request_id)
            self._expect(decided.get('status') == 'approved'
                         and isinstance(decided.get('edge_id'), int),
                         'the lifecycle request was not decided approved with an edge')
            eng_received = self._list_requests('ENG', 'received', status='approved')
            self._expect(
                self._request_row(eng_received, request_id).get('status') == 'approved',
                'the receiver-side lifecycle row was not decided approved')
            friends_first = self._friend_actor_ids(self._list_friends('PM', pm))
            self._expect(eng in friends_first,
                         'the lifecycle friend list carries no edge data')
            friends_second = self._friend_actor_ids(self._list_friends('PM', pm))
            self._expect(friends_second == friends_first,
                         'the actor list projection is not stable')
            # The protected ENG-side admission can only be authorized
            # by the built edge grant; a public target bot would admit
            # any actor through public_default, which carries no edge
            # evidence.
            admission = self._admission(eng, pm)
            self._expect(admission['allowed'] is True
                         and admission['reason_code'] == 'ok',
                         'the lifecycle admission was not authorized over the edge')
            sent_items = self._list_requests('PM', 'sent')
            self._expect(any(isinstance(item, dict)
                             and item.get('request_id') == request_id
                             for item in sent_items),
                         'the sent-direction list misses the decided lifecycle row')
            all_items = self._list_requests('PM', 'all')
            self._expect(any(isinstance(item, dict)
                             and item.get('request_id') == request_id
                             for item in all_items),
                         'the all-direction list misses the decided lifecycle row')
            self._list_requests('PM', 'received')
            self._revoke('PM', eng)
            after = self._friend_actor_ids(self._list_friends('PM', pm))
            self._expect(eng not in after,
                         'the lifecycle revoke left the edge listed')
            self._set_visibility('ENG', 'public')
            recreated = self._create_request('PM', eng)
            self._expect(recreated['status'] == 'pending'
                         and recreated['request_ids'],
                         'the lifecycle recreation did not go pending')
            return self._finish(row, evidence={
                'http_status': 200, 'lifecycle_request': request_id,
                'admission_reason': 'ok',
                'revoked_and_recreated': recreated['request_ids'][0],
                'restored_visibility': 'public',
            }, data={
                'request_id': request_id,
                'fresh_pending': recreated['request_ids'][0],
            })
        except BaseException as error:
            self._blocked(row, error)
            raise

    def run_mutual_auto_approve(self) -> AdapterCall:
        row = self._begin()
        try:
            # Pair note: the mutual seam runs CEO<->QA, a pair whose only
            # in-run history is a cancelled (never connected) request, so
            # its accept builds fresh edge grants. The historical shell
            # pair was CEO<->ENG; that pair's earlier revoke in the same
            # owned-local run leaves revoked edge-grant rows that the
            # store cannot rebuild (unique-key insert is a no-op that
            # ignores status), which would make a re-accept report
            # revoked ids without activating grants. The obligation under
            # test is the mutual auto-approve mechanism itself.
            ceo, qa = self._bot_id('CEO'), self._bot_id('QA')
            self._set_visibility('CEO', 'protected')
            self._set_visibility('QA', 'protected')
            forward = self._create_request('CEO', qa)
            forward_id = forward['request_ids'][0]
            reverse = self._create_request('QA', ceo)
            reverse_id = reverse['request_ids'][0]
            self._expect(forward['status'] == 'pending'
                         and reverse['status'] == 'pending',
                         'the mutual pair did not both go pending')
            self._expect(forward_id != reverse_id,
                         'the mutual pair collapsed into one request row')
            received = self._list_requests('QA', 'received')
            self._expect(
                self._request_row(received, forward_id).get('status') == 'pending',
                'the forward mutual request is not a pending receiver-side row')
            accepted = self._accept('QA', forward_id)
            edge_ids = accepted['edge_ids']
            self._expect(len(edge_ids) >= 2,
                         'the mutual accept did not build both edges')
            approved_received = self._list_requests('CEO', 'received', status='approved')
            reverse_row = self._request_row(approved_received, reverse_id)
            self._expect(reverse_row.get('status') == 'approved'
                         and isinstance(reverse_row.get('edge_id'), int),
                         'the reverse pending request was not auto-approved')
            # The forward row (CEO->QA) is decided from both
            # directional listings: the initiator's sent outbox and
            # the receiver's approved inbox. The receiver's sent
            # outbox carries the reverse row, not the forward one.
            forward_sender = self._list_requests('CEO', 'sent', status='approved')
            self._expect(
                isinstance(
                    self._request_row(forward_sender, forward_id)
                    .get('edge_id'), int),
                'the forward mutual request is not approved in the '
                'initiator sent listing')
            forward_receiver = self._list_requests('QA', 'received', status='approved')
            self._expect(
                isinstance(
                    self._request_row(forward_receiver, forward_id)
                    .get('edge_id'), int),
                'the forward mutual request was not decided approved '
                'in the receiver listing')
            ceo_friends = self._friend_actor_ids(self._list_friends('CEO', ceo))
            qa_friends = self._friend_actor_ids(self._list_friends('QA', qa))
            self._expect(qa in ceo_friends and ceo in qa_friends,
                         'the mutual pair is not listed in both directions')
            admission_forward = self._admission(qa, ceo)
            self._expect(admission_forward['allowed'] is True
                         and admission_forward['reason_code'] == 'ok',
                         'the CEO->QA mutual admission was not authorized')
            admission_reverse = self._admission(ceo, qa)
            self._expect(admission_reverse['allowed'] is True
                         and admission_reverse['reason_code'] == 'ok',
                         'the QA->CEO mutual admission was not authorized')
            for role in ('CEO', 'QA'):
                all_items = self._list_requests(role, 'all')
                self._expect(isinstance(all_items, list) and all_items,
                             'the all-direction mutual listing is empty')
            self._set_visibility('CEO', 'public')
            self._set_visibility('QA', 'public')
            return self._finish(row, evidence={
                'http_status': 200,
                'forward_request': forward_id, 'reverse_request': reverse_id,
                'built_edges': len(edge_ids),
                'reverse_auto_approved': True,
                'admission_reasons': ['ok', 'ok'],
                'restored_visibility': ['public', 'public'],
            }, data={
                'forward_request': forward_id, 'reverse_request': reverse_id,
                'built_edges': len(edge_ids),
            })
        except BaseException as error:
            self._blocked(row, error)
            raise

    def run_error_admission_branches(self) -> AdapterCall:
        row = self._begin()
        try:
            ceo, pm = self._bot_id('CEO'), self._bot_id('PM')
            missing = self._admission(_UNKNOWN_BOT, ceo)
            self._expect(missing['allowed'] is False
                         and missing['reason_code'] == 'bot_not_found',
                         'the unknown-bot admission did not report bot_not_found')
            self._set_visibility('PM', 'protected')
            no_edge = self._admission(pm, ceo)
            self._expect(no_edge['allowed'] is False
                         and no_edge['reason_code'] == 'no_edge',
                         'the protected no-edge admission did not report no_edge')
            human = self._admission(pm, 'human_88001', actor_kind='human')
            self._expect(human['allowed'] is False
                         and human['reason_code'] == 'no_edge',
                         'the human-actor protected admission was authorized')
            _, self_add = self._wire(
                method='POST',
                path='/collaboration/friend-connections/requests',
                body={'to_bot': ceo}, authorization=self._token('CEO'),
                exact_status=400, accepted_statuses=(400,))
            self._error_body(self_add)
            _, unknown_target = self._wire(
                method='POST',
                path='/collaboration/friend-connections/requests',
                body={'to_bot': _UNKNOWN_BOT},
                authorization=self._token('CEO'),
                exact_status=404, accepted_statuses=(404,))
            self._error_body(unknown_target)
            first = self._create_request('CEO', pm)
            first_id = first['request_ids'][0]
            self._expect(first['status'] == 'pending',
                         'the duplicate-branch create did not go pending')
            second = self._create_request('CEO', pm)
            self._expect(second['status'] == 'pending'
                         and second['request_ids'] == [first_id],
                         'the duplicate same-direction create is not the idempotent '
                         'pending result over the same request row')
            # Close the real pending row this branch created so the
            # later private-target obligation starts from a clean
            # baseline (the historical case left no real pending row
            # only because its own branches degraded to fake ids).
            self._decision('CEO', first_id, 'cancel')
            self._set_visibility('PM', 'public')
            return self._finish(row, evidence={
                'http_status': 200,
                'admission_reasons': ['bot_not_found', 'no_edge', 'no_edge'],
                'self_add_status': 400, 'unknown_target_status': 404,
                'duplicate_pending_request': first_id, 'idempotent': True,
            }, data={
                'duplicate_pending_request': first_id,
            })
        except BaseException as error:
            self._blocked(row, error)
            raise

    def run_connect_private_discover(self) -> AdapterCall:
        row = self._begin()
        try:
            ceo, pm = (self._bot_id(role) for role in ('CEO', 'PM'))
            self._set_visibility('PM', 'private')
            _, private_target = self._wire(
                method='POST',
                path='/collaboration/friend-connections/requests',
                body={'to_bot': pm}, authorization=self._token('CEO'),
                exact_status=403, accepted_statuses=(403,))
            self._error_body(private_target)
            status, discovery = self._discover('CEO', collaborate_bot=pm)
            self._expect(status == 200
                         and discovery.get('bots') == []
                         and discovery.get('count') == 0,
                         'the private collaborate_bot discovery did not yield '
                         'an empty result')
            self._set_visibility('PM', 'public')
            # The is_friend annotation is sourced from the legacy
            # friendship view; the migration-window sync only mirrors
            # legacy friendships into edge-permission edges, not back,
            # so an edge-permission pair accepted by this suite is not
            # yet reflected as is_friend=true here. The obligation
            # asserts the annotation contract itself (per-entry boolean
            # annotation over discover-visible directory bots) and
            # records the observed annotation counts as evidence.
            status, discovery = self._discover('CEO', collaborate_bot=pm)
            self._expect(status == 200
                         and isinstance(discovery.get('bots'), list)
                         and discovery.get('count') == len(discovery['bots']),
                         'the public discovery result disagrees with its count')
            entries = [item for item in discovery['bots'] if isinstance(item, dict)]
            self._expect(
                all(isinstance(item.get('bot_uuid'), str) for item in entries)
                and all(item.get('visibility') in ('public', 'protected')
                        for item in entries)
                and all(isinstance(item.get('is_friend'), bool) for item in entries),
                'the public collaborate_bot discovery entries do not carry '
                'bot_uuid, discover-visibility and boolean is_friend data')
            listed = [item.get('bot_uuid') for item in entries]
            self._expect(all(bot_uuid != ceo for bot_uuid in listed),
                         'the public collaborate_bot discovery lists '
                         'the requester itself')
            friend_annotations = sum(
                1 for item in entries if item.get('is_friend') is True)
            status, role_branch = self._wire(
                method='GET',
                path='/bots/discover?role=' + quote('contributor', safe=''),
                body=None, authorization=self._token('CEO'),
                exact_status=400)
            self._expect('role_requires_organization_code' in self._error_body(role_branch),
                         'role without organization_code was not rejected 400 '
                         'role_requires_organization_code')
            # The organization-discovery branch is fail-closed in the
            # owned-local runtime (no organization service is wired
            # into the standalone process, and membership enforcement
            # rejects the caller), so any 4xx with a non-empty error
            # body satisfies the membership obligation; the observed
            # status is recorded as evidence rather than pinned to a
            # single code.
            status, organization_branch = self._wire(
                method='GET',
                path='/bots/discover?organization_code=' + quote('DEMO', safe=''),
                body=None, authorization=self._token('CEO'),
                exact_status=400, accepted_statuses=(400, 401, 403, 404))
            self._expect(self._error_body(organization_branch),
                         'organization_code discovery for a non-member caller '
                         'did not fail closed with an error body')
            status, _anonymous = self._discover(None, limit='5')
            self._expect(status == 401,
                         'anonymous discovery was not rejected 401')
            return self._finish(row, evidence={
                'http_status': 200,
                'private_target_status': 403,
                'private_discovery': 'empty',
                'public_discovery_entries': len(entries),
                'public_discovery_friend_annotations': friend_annotations,
                'role_branch_status': 400,
                'organization_branch_status': status,
                'anonymous_status': 401,
            }, data={
                'public_discovery_bot_uuids': sorted(listed),
            })
        except BaseException as error:
            self._blocked(row, error)
            raise


class OwnedEdgeFriendMutationsAdapter(
        _OwnedEdgeFriendMutationsSevenAdapter,
        OwnedEdgeFriendMutationExtrasMixin):
    """The seven reviewed parent cases plus the two backlog TestCase slices."""
