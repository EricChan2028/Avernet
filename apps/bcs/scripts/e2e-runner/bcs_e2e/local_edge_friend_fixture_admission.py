"""Owned service-key fixture admission for the Edge friend-mutation suite.

The class owns a one-time service credential and every admin ensure it uses in
one live, empty local registry. No token, Cookie, principal or service key is
allowed into evidence; each receipt records only the sanitized outcome and the
exact Bot/Case scope.
"""
from __future__ import annotations

import json
import secrets
from http.client import HTTPConnection
from urllib.parse import quote

from .edge_friend_mutation_case import BACKLOG_CASE_IDS
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

_MAX_BODY_BYTES = 64 * 1024
_TRANSPORT_TIMEOUT_SECONDS = 10.0


class OwnedEdgeFriendFixtureAdmission:
    """Owns the single service-credential admin ensure of the ensure-bot
    obligation. The key is generated for this live, empty local registry."""

    def __init__(self, runtime: OwnedBcsProcess, *, bot_id: str):
        if type(runtime) is not OwnedBcsProcess:
            raise RunStateError('Edge friend-mutation fixture admission requires the exact owned runtime')
        if (runtime.inputs is None or runtime.state != 'RUNNING'
                or runtime.ready is not True or runtime.child is None
                or runtime.child.poll() is not None):
            raise RunStateError('Edge friend-mutation fixture admission requires a live owned runtime')
        self.runtime = runtime
        self.bot_id = identifier(bot_id, 'Edge friend-mutation fixture Bot ID')
        self.path = runtime.root / 'edge-friend-ensure-admission.json'
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Edge friend-mutation fixture admission was already attempted')
        self.attempted = False
        self._service_key = 'e2e-' + secrets.token_hex(32)
        self.state = {
            'schema_version': 1, 'status': 'PENDING',
            'requirement_id': 'EDGE_FRIEND_MUTATIONS.ensure-bot',
            'case_id': 'LOCAL-EDGE-ENSURE-BOT-01',
            'actor': 'SERVICE_KEY', 'effect': 'MUTATION',
            'ownership_scope': 'CASE_SEAM', 'run_id': runtime.run_id,
            'bot_uuid': self.bot_id, 'staff_no': '',
            'api_face': 'legacy-local-admin', 'http_method': 'POST',
            'service_key_in_evidence': False,
        }

    def _save(self) -> None:
        encoded = json.dumps(self.state, ensure_ascii=True, sort_keys=True, allow_nan=False)
        if self._service_key in encoded:
            raise RunStateError('Edge friend-mutation fixture receipt must not expose its service key')
        write(self.path, self.state)

    def validate_for_runtime(self, runtime: OwnedBcsProcess) -> None:
        if (type(runtime) is not OwnedBcsProcess or runtime is not self.runtime
                or self.attempted is not True
                or self.state.get('status') != 'PASS'
                or runtime.root != self.runtime.root
                or runtime.run_id != self.runtime.run_id
                or fingerprint(read(self.path)) != fingerprint(self.state)):
            raise RunStateError('Edge friend-mutation fixture admission ownership differs')

    def admit(self, *, allow_write: bool) -> dict:
        if allow_write is not True:
            raise RunStateError('Edge friend-mutation fixture admission requires explicit write permission')
        if self.attempted:
            raise RunStateError('Edge friend-mutation fixture admission cannot be retried')
        self.attempted = True
        self.state['status'] = 'APPLYING'
        self._save()
        body = json.dumps({'name': self.bot_id, 'staff_no': ''},
                          ensure_ascii=True, sort_keys=True).encode('ascii')
        headers = {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-Request-ID': 'e2e-edge-friend-ensure-' + self.runtime.run_id,
            # The created key is internal to this live, empty local registry.
            'X-BCS-Service-Key': self._service_key,
        }
        connection = HTTPConnection('127.0.0.1', self.runtime.port, timeout=_TRANSPORT_TIMEOUT_SECONDS)
        try:
            connection.request('POST', '/admin/bots/' + quote(self.bot_id, safe='') + '/ensure',
                               body=body, headers=headers)
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
                        raise RunStateError('Edge friend-mutation fixture response exceeds its byte budget')
                if response.length not in (None, 0):
                    raise RunStateError('Edge friend-mutation fixture response was truncated')
            if status != 200:
                raise RunStateError('Edge friend-mutation fixture ensure was not accepted')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            if (not isinstance(value, dict) or value.get('ensured') is not True
                    or value.get('created') is not False
                    or value.get('bot_uuid') != self.bot_id):
                raise RunStateError('Edge friend-mutation fixture ensure contract differs for an existing bot')
            self.state.update(http_status=status, ensured=True, created=False, status='PASS')
            self._save()
            return dict(self.state)
        except BaseException:
            self.state['status'] = 'BLOCKED'
            self.state['service_key_in_evidence'] = False
            try:
                self._save()
            except BaseException:
                pass
            raise
        finally:
            connection.close()



    def ensure_case_bot(self, *, case_id: str, bot_id: str, staff_no: str,
                        visibility: str, user_visibility: str | None = None,
                        allow_write: bool) -> dict:
        """Perform one additional exact-ID admin ensure for a Case-owned Bot."""
        case_id = identifier(case_id, 'Edge friend-mutation dependent Case ID')
        bot_id = identifier(bot_id, 'Edge friend-mutation Case Bot ID')
        staff_no = identifier(staff_no, 'Edge friend-mutation Case Bot owner')
        visibility = identifier(visibility, 'Edge friend-mutation visibility')
        if user_visibility is not None:
            user_visibility = identifier(user_visibility, 'Edge friend-mutation user visibility')
        if (case_id not in BACKLOG_CASE_IDS
                or visibility not in ('private', 'protected', 'public')
                or (user_visibility is not None and user_visibility not in ('private', 'protected', 'public'))):
            raise RunStateError('Edge friend-mutation Case ensure scope differs from the backlog contract')
        if allow_write is not True:
            raise RunStateError('Edge friend-mutation Case ensure requires explicit write permission')
        if self.state.get('status') != 'PASS':
            raise RunStateError('Edge friend-mutation Case ensure requires a completed base fixture admission')
        existing = [dict(row) for row in self.state.get('case_fixture_ensures', [])
                    if row.get('case_id') == case_id]
        if existing:
            raise RunStateError('Edge friend-mutation Case ensure cannot be retried')
        if 'case_fixture_ensures' not in self.state:
            self.state['case_fixture_ensures'] = []
        receipt = {
            'case_id': case_id, 'bot_uuid': bot_id, 'staff_no': staff_no,
            'visibility': visibility,
            **({'user_visibility': user_visibility} if user_visibility is not None else {}),
            'http_status': None, 'ensured': False, 'created': None,
            'service_key_in_evidence': False,
        }
        self.state['case_fixture_ensures'].append(receipt)
        self._save()
        body = json.dumps({'name': 'Edge friend-mutation case Bot', 'staff_no': staff_no,
                           'visibility': visibility,
                           **({'user_visibility': user_visibility} if user_visibility is not None else {})},
                          ensure_ascii=True, sort_keys=True).encode('utf-8')
        headers = {
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'X-Request-ID': 'e2e-edge-friend-' + self.runtime.run_id + '-' + case_id,
            'X-BCS-Service-Key': self._service_key,
        }
        connection = HTTPConnection('127.0.0.1', self.runtime.port,
                                    timeout=_TRANSPORT_TIMEOUT_SECONDS)
        try:
            connection.request('POST', '/admin/bots/' + quote(bot_id, safe='') + '/ensure',
                                body=body, headers=headers)
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
                        raise RunStateError('Edge friend-mutation Case ensure response exceeds its byte budget')
                if response.length not in (None, 0):
                    raise RunStateError('Edge friend-mutation Case ensure response was truncated')
            if status != 200:
                raise RunStateError('Edge friend-mutation Case ensure was not accepted')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            if (not isinstance(value, dict) or value.get('ensured') is not True
                    or type(value.get('created')) is not bool
                    or value.get('bot_uuid') != bot_id):
                raise RunStateError('Edge friend-mutation Case ensure contract differs')
            receipt.update(http_status=status, ensured=True,
                           created=value['created'], status='PASS')
            self._save()
            return dict(receipt)
        except BaseException:
            receipt['status'] = 'BLOCKED'
            try:
                self._save()
            except BaseException:
                pass
            raise
        finally:
            connection.close()
