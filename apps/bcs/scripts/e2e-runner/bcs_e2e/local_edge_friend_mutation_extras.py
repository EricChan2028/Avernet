"""The two backlog Edge TestCase adapters kept out of the reviewed reviewed-release-parent set.

They live in the same owned runtime and journal shape as the release parent
cases, but remain independent slices: they do not enlarge the reviewed
release denominator and never grant release credit.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from http.client import HTTPConnection
from urllib.parse import quote, urlsplit

from .case_adapters import AdapterCall
from .profile import identifier, unique_keys
from .run_state import RunStateError

_MAX_BODY_BYTES = 64 * 1024
_TRANSPORT_TIMEOUT_SECONDS = 10.0
_TC_BOT_PREFIX = '2026e2e-tc-bot:'
_TC_OWNER = '85020'
_FRIEND_EXT_BOT = 'e2e-friend-ext-bot'
_MIXED_SCOPE = {
    'friend_check_in_strategy': 'DEPT_FREE',
    'friend_ext': {
        'public_user_approval': {'status': 'AGREE'},
        'no_check_scope_friend_deps': [
            {'deptNo': 'L3480', 'deptName': '蚂蚁集团-信贷事业群'},
            'F4858',
        ],
        'view_scope_user_friend_deps': [],
        'view_scope_agent_friend_deps': [],
    },
}
_SCOPED_SHAPE = {
    'friend_ext': {
        'public_user_approval': {'status': 'AGREE'},
        'no_check_scope_friend_deps': [
            {'deptNo': 'L3480', 'deptName': '蚂蚁集团-信贷事业群'},
            'F4858',
        ],
        'view_scope_user_friend_deps': [
            {'deptNo': 'L3480'},
            {'priority': 7},
        ],
        'view_scope_agent_friend_deps': [
            {'deptNo': 'L3480'},
            {'priority': 7},
        ],
    },
}


class OwnedEdgeFriendMutationExtrasMixin:
    """Implement the two backlog obligations against the same adapter base."""

    def _principal_token(self, *, user_id: str, username: str,
                         issuer: str) -> str:
        key = self.runtime.inputs.environment.get('AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE')
        if not isinstance(key, str) or not key:
            raise RunStateError('owned runtime lost its principal signing-key reference')
        now = int(time.time())
        header = {'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}
        claims = {
            'iss': issuer, 'aud': 'bcs', 'iat': now, 'exp': now + 60,
            'principals': [{
                'type': 'user', 'tenant': 'bcs-e2e',
                'subject': {
                    'id': user_id, 'username': username,
                    'display_name': None, 'full_name': None,
                    'tenant_id': 'bcs-e2e',
                },
            }],
        }
        encode = lambda value: base64.urlsafe_b64encode(
            json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        ).rstrip(b'=').decode('ascii')
        signing_input = (encode(header) + '.' + encode(claims)).encode('ascii')
        digest = hmac.new(key.encode(), signing_input, hashlib.sha256).digest()
        return signing_input.decode('ascii') + '.' + (
            base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii'))

    def _principal_claims(self, token: str) -> dict:
        try:
            payload = token.split('.')[1]
            payload += '=' * (-len(payload) % 4)
            value = json.loads(base64.urlsafe_b64decode(payload), object_pairs_hook=unique_keys)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except BaseException as error:
            raise RunStateError('friend-auth-sync principal is undecodable') from error

    def _mock_request(self, *, method: str, path: str,
                      body: dict | None = None) -> dict:
        origin = self.runtime.friend_work_order_base_url
        if not isinstance(origin, str):
            raise RunStateError('friend-auth-sync mock origin is not bound to the runtime')
        parsed = urlsplit(origin)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                or parsed.port is None or parsed.path or parsed.query or parsed.fragment):
            raise RunStateError('friend-auth-sync mock origin is outside its loopback contract')
        payload = None if body is None else json.dumps(
            body, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if payload is not None and len(payload) > _MAX_BODY_BYTES:
            raise RunStateError('friend-auth-sync mock request exceeds its byte budget')
        headers = {'Accept': 'application/json',
                   'X-Request-ID': 'e2e-edge-friend-' + self.run_id + '-mock'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        connection = HTTPConnection(parsed.hostname, parsed.port,
                                    timeout=_TRANSPORT_TIMEOUT_SECONDS)
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
                        raise RunStateError('friend-auth-sync mock response exceeds its byte budget')
                if response.length not in (None, 0):
                    raise RunStateError('friend-auth-sync mock response was truncated')
            if status != 200:
                raise RunStateError('friend-auth-sync mock control plane returned ' + str(status))
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            if not isinstance(value, dict):
                raise RunStateError('friend-auth-sync mock response is not an object')
            return value
        except RunStateError:
            raise
        except BaseException as error:
            raise RunStateError('friend-auth-sync mock exchange failed; outcome unknown') from error
        finally:
            connection.close()

    def _forwarded_request(self, *, method: str, path: str, body: dict | None,
                           principal: str, mock_user_id: str | None = None,
                           exact_status: int,
                           accepted_statuses: tuple[int, ...] | None = None) -> tuple[int, dict]:
        headers = {'X-Avernet-Principal': principal}
        if mock_user_id is not None:
            headers['X-Mock-User-Id'] = identifier(mock_user_id, 'owned local human ID')
        return self._wire(method=method, path=path, body=body,
                          exact_status=exact_status,
                          accepted_statuses=accepted_statuses,
                          extra_headers=headers)

    def run_tc_friend_auth_sync_readdress(self) -> AdapterCall:
        row = self._begin()
        try:
            tc_bot = _TC_BOT_PREFIX + _TC_OWNER
            self.fixture_admission.ensure_case_bot(
                case_id=self.case_id, bot_id=tc_bot, staff_no=_TC_OWNER,
                visibility='protected', user_visibility='protected',
                allow_write=True)
            self._mock_request(method='POST',
                               path='/control/friend-auth-sync/clear', body={})
            approver = '153998'
            principal = self._principal_token(user_id=approver,
                                                username='tc-sync-approver',
                                                issuer='backend')
            _, created = self._forwarded_request(
                method='POST', path='/collaboration/friend-connections/requests',
                body={'to_bot': tc_bot, 'message': 'owned-local tc friend-auth sync'},
                principal=principal, mock_user_id=approver, exact_status=200)
            payload = created.get('data') or {}
            request_ids = payload.get('request_ids') if isinstance(payload, dict) else None
            if (not isinstance(request_ids, list) or not request_ids
                    or not all(isinstance(item, str) and item for item in request_ids)
                    or payload.get('status') != 'pending'):
                raise RunStateError('TC friend request did not create one pending row')
            request_id = request_ids[0]
            _, accepted = self._forwarded_request(
                method='POST',
                path='/collaboration/friend-connections/requests/'
                     + quote(identifier(request_id, 'TC friend request ID'), safe='') + '/accept',
                body={}, principal=principal, mock_user_id=approver,
                exact_status=200)
            accepted_data = accepted.get('data') or {}
            if (not isinstance(accepted_data, dict)
                    or not isinstance(accepted_data.get('edge_ids'), list)
                    or not accepted_data['edge_ids']):
                raise RunStateError('TC friend accept did not build an edge')
            view = self._mock_request(method='GET',
                                      path='/control/friend-auth-sync/requests')
            requests = view.get('requests')
            if not isinstance(requests, list) or len(requests) != 1:
                raise RunStateError('TC friend-auth sync did not produce exactly one bounded call')
            record = requests[0]
            body = record.get('body') if isinstance(record, dict) else None
            if (not isinstance(body, dict) or body.get('bot_id') != _TC_BOT_PREFIX[:-1]
                    or body.get('owner_work_no') != _TC_OWNER
                    or body.get('action') != 'grant'):
                raise RunStateError('TC friend-auth sync body differs from the frozen contract')
            claims = self._principal_claims(record.get('principal') or '')
            principals = claims.get('principals')
            subject = principals[0].get('subject', {}) if (
                isinstance(principals, list) and principals
                and isinstance(principals[0], dict)) else {}
            if (claims.get('iss') != 'bcs' or claims.get('aud') != 'backend'
                    or subject.get('id') != approver):
                raise RunStateError('TC principal re-address did not preserve the approver')
            return self._finish(row, evidence={
                'http_status': 200, 'request_id': request_id,
                'sync_call_count': 1, 'sync_issuer': 'bcs',
                'sync_audience': 'backend', 'approver_preserved': True,
                'bot_id': tc_bot, 'owner_work_no': _TC_OWNER,
            }, data={
                'request_id': request_id, 'sync_call_count': 1,
                'bot_id': tc_bot, 'owner_work_no': _TC_OWNER,
            })
        except BaseException as error:
            self._blocked(row, error)
            raise

    def run_dept_scope_friend_ext(self) -> AdapterCall:
        row = self._begin()
        try:
            self.fixture_admission.ensure_case_bot(
                case_id=self.case_id, bot_id=_FRIEND_EXT_BOT,
                staff_no=_TC_OWNER, visibility='protected',
                allow_write=True)
            owner_principal = self._principal_token(user_id=_TC_OWNER,
                                                      username='friend-ext-owner',
                                                      issuer='gateway')
            owner_headers = {'X-Avernet-Principal': owner_principal}
            _, _ = self._wire(
                method='PATCH',
                path='/openapi/v1/collaboration/bots/' + quote(_FRIEND_EXT_BOT, safe=''),
                body=_MIXED_SCOPE, extra_headers=owner_headers,
                exact_status=200)
            applicant = '153998'
            applicant_principal = self._principal_token(
                user_id=applicant, username='friend-ext-object-parser',
                issuer='gateway')
            self._forwarded_request(
                method='POST', path='/collaboration/friend-connections/requests',
                body={'to_bot': _FRIEND_EXT_BOT,
                      'message': 'owned-local object friend_ext parse'},
                principal=applicant_principal, mock_user_id=applicant,
                exact_status=200)
            scoped_status, _ = self._wire(
                method='PATCH',
                path='/openapi/v1/collaboration/bots/' + quote(_FRIEND_EXT_BOT, safe=''),
                body=_SCOPED_SHAPE, extra_headers=owner_headers,
                exact_status=200)
            second_status, _ = self._forwarded_request(
                method='POST', path='/collaboration/friend-connections/requests',
                body={'to_bot': _FRIEND_EXT_BOT,
                      'message': 'owned-local scoped friend_ext parse'},
                principal=applicant_principal, mock_user_id='511488',
                exact_status=403, accepted_statuses=(200, 403))
            bot_status, _ = self._wire(
                method='POST', path='/collaboration/friend-connections/requests',
                body={'to_bot': _FRIEND_EXT_BOT},
                authorization=self._token('PM'),
                exact_status=403, accepted_statuses=(200, 403))
            return self._finish(row, evidence={
                'http_status': scoped_status,
                'owner_patch_status': 200, 'human_object_status': 200,
                'human_scoped_status': second_status,
                'bot_scoped_status': bot_status,
                'scope_shapes': ['object', 'legacy-string', 'garbage'],
                'parser_paths': ['human-no-check', 'human-user-scope', 'bot-agent-scope'],
                'bot_uuid': _FRIEND_EXT_BOT,
            }, data={
                'human_scoped_status': second_status,
                'bot_scoped_status': bot_status,
            })
        except BaseException as error:
            self._blocked(row, error)
            raise
