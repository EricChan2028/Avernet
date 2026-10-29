"""Finite owned-local legacy friendship sequence, with optional actual CLI steps.

Every mutation has a durable intent. Unknown outcomes are retained, never retried
or repaired. This code has no remote profile, DB writes or relationship delete.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request

from .correlation import record_response_id
from .errors import has_cancellation, primary_error
from .local_role_identity import OwnedLocalRoleIdentity, LocalRoleIdentityError
from .local_sqlite import OwnedSqliteDatabase
from .profile import fingerprint, identifier, unique_keys
from .run_state import read, write
from .trust_baseline import ROLES, visibility


class LocalTrustApiError(RuntimeError):
    pass


PAIRS = (('CEO', 'ENG', 'accepted'), ('QA', 'PM', 'accepted'), ('ENG', 'CS', 'rejected'))


class OwnedLocalTrustApi:
    def __init__(self, identities: OwnedLocalRoleIdentity, *, cli=None):
        if type(identities) is not OwnedLocalRoleIdentity or identities.fixture_set != 'trusted-team':
            raise LocalTrustApiError('friendship API slice requires managed owned-local trusted-team identities')
        if cli is not None:
            from .local_trust_cli import OwnedLocalTrustCli
            if type(cli) is not OwnedLocalTrustCli:
                raise LocalTrustApiError('CLI execution requires the controlled product adapter')
        self.registration = identities.fixtures.registration
        if cli is not None and (self.registration is None or self.registration.context is None):
            raise LocalTrustApiError('CLI parent execution requires a frozen registration before fixtures')
        self.cli = cli
        self.case_id = 'LOCAL-TRUST-API-CLI-01' if cli is not None else 'LOCAL-TRUST-API-01'
        self.pairs = PAIRS + ((('CEO', 'PM', 'accepted'),) if cli is not None else ())
        self.identities = identities
        self.runtime = identities.runtime
        self.path = self.runtime.root / 'local-trust-api.json'
        self.state = None
        self._digest = ''
        self.invalidated = False
        self.journal_integrity_lost = False

    def _active(self):
        if self.invalidated:
            raise LocalTrustApiError('friendship mutation authority was invalidated')
        if self.registration is not None and (self.cli is None or self.cli.digest != self.registration.cli_sha256):
            raise LocalTrustApiError('CLI binary identity differs from the frozen registration')
        self.identities._require_roles(ROLES)
        self._check_journal()

    def _check_journal(self):
        if self.journal_integrity_lost:
            raise LocalTrustApiError('friendship journal integrity is lost; original artifact is preserved')
        if self._digest:
            try:
                matches=fingerprint(read(self.path))==self._digest
            except Exception:
                matches=False
            if not matches:
                self.invalidated=True
                self.journal_integrity_lost=True
                raise LocalTrustApiError('friendship journal changed or became unreadable during execution')

    def _save(self):
        self._check_journal()
        write(self.path, self.state)
        self._digest = fingerprint(self.state)

    def _integrity_outcome(self, cancelled):
        path=self.runtime.root/'local-trust-api-integrity.json'
        outcome={'schema_version':1,'run_id':self.runtime.run_id,'case_id':self.case_id,
                 'status':'CANCELLED' if cancelled else 'BLOCKED','ready_to_execute':False,
                 'journal_integrity':'CHANGED_OR_UNREADABLE','journal_overwritten':False,
                 'resource_disposition':'RETAINED_LOCAL_DATABASE','automatic_relation_delete':False}
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write((json.dumps(outcome,sort_keys=True)+'\n').encode())
            stream.flush();os.fsync(stream.fileno())

    def _id(self, role):
        self._active()
        return self.identities._entries[role][0]

    def _read(self, role, operation):
        self._active()
        entry = self.identities._entries[role]
        bot_id, token = entry
        value = self.identities._request(operation, bot_id, token)
        self._active()
        return value

    def _mutate(self, role, operation, *, peer='', request_id='', desired=''):
        self._active()
        if len(self.state['operations']) >= 11:
            raise LocalTrustApiError('friendship mutation budget exhausted')
        entry = self.identities._entries[role]
        bot_id, token = entry
        if operation in ('visibility.set', 'visibility.restore'):
            if role not in ('ENG', 'PM', 'CS') or desired not in ('public', 'protected', 'private'):
                raise LocalTrustApiError('visibility mutation is outside the owned role contract')
            original = next(item['original_visibility'] for item in self.state['baseline']['roles'] if item['role']==role)
            expected = {'ENG':'public','PM':'protected','CS':'protected'}[role] if operation=='visibility.set' else original
            if (desired != expected or any(op['operation']==operation and op['role']==role for op in self.state['operations'])
                    or operation=='visibility.restore' and not any(op['operation']=='visibility.set' and op['role']==role
                        and op['status']=='ACKNOWLEDGED' for op in self.state['operations'])):
                raise LocalTrustApiError('visibility write must follow the exact initial/restore plan without replay')
            method, path, body = 'PUT', '/bots/' + quote(bot_id, safe='') + '/visibility', {'visibility': desired}
        elif operation == 'friend.request':
            if (role, peer) not in {(a,b) for a,b,_ in PAIRS}:
                raise LocalTrustApiError('friend request is outside the finite pair contract')
            if any(op['operation'] == operation and op['role'] == role and op['peer'] == peer
                   for op in self.state['operations']):
                raise LocalTrustApiError('friend creation cannot be retried')
            method, path, body = 'POST', '/friends/request', {'to_bot': self._id(peer)}
        elif operation in ('friend.accept', 'friend.reject'):
            known = next((item for item in self.state['requests'] if item['request_id'] == request_id), None)
            if (known is None or known['receiver'] != role or known['status'] != 'pending'
                    or (known['sender'], role) not in {('QA','PM'),('ENG','CS')}
                    or operation != ('friend.accept' if role == 'PM' else 'friend.reject')
                    or any(op['operation'] in ('friend.accept','friend.reject') and op['request_id']==request_id
                           for op in self.state['operations'])):
                raise LocalTrustApiError('decision requires the exact owned pending request and receiver')
            identifier(request_id, 'owned friendship request ID')
            method, path, body = 'POST', '/friends/requests/' + quote(request_id, safe='') + '/' + operation.split('.')[1], {}
        else:
            raise LocalTrustApiError('unreviewed friendship mutation')
        # Recheck exact Bot authority immediately before each intended write;
        # legacy mutation handlers can otherwise fall back to a local Human.
        # This remains non-atomic under the documented no-concurrent-token-change
        # assumption, never a remote authentication workaround.
        visibility(self._read(role,'visibility'),bot_id)
        friends=self._read(role,'friends').get('data')
        if not isinstance(friends,list) or len(friends)>=100 or any(not isinstance(row,dict) for row in friends):
            raise LocalTrustApiError('pre-mutation Bot self-read is invalid or incomplete')
        visibility(self._read(role,'visibility'),bot_id)
        if self.identities._entries[role] != entry:
            raise LocalTrustApiError('role credential changed during mutation admission')
        correlation = 'e2e-trust-' + uuid.uuid4().hex
        event = {'operation': operation, 'role': role, 'peer': peer, 'request_id': request_id,
                 'desired': desired, 'method': method, 'path': path,
                 'submitted_request_id': correlation, 'status': 'INTENDED',
                 **({'resource_binding': 'UNKNOWN'} if operation == 'friend.request' else {})}
        self.state['operations'].append(event)
        self._save()  # A failed intent write prevents dispatch.
        self._active()
        request = Request(self.identities.origin + path, method=method,
                          data=json.dumps(body, separators=(',', ':')).encode(),
                          headers={'Accept':'application/json', 'Content-Type':'application/json',
                                   'Authorization':'Bearer ' + token, 'X-Request-ID':correlation})
        try:
            response = self.identities.opener.open(request, timeout=5)
            with response:
                event['http_status'] = response.status
                record_response_id(event, response.headers.get('x-request-id'), source='HEADER', sensitive_values=(token,))
                if response.status != (201 if operation == 'friend.request' and peer != 'ENG' else 200):
                    raise LocalTrustApiError('friendship mutation was not confirmed')
                raw = response.read(65_537)
            self._active()
            if len(raw) > 65_536:
                raise LocalTrustApiError('friendship response exceeds its byte budget')
            result = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if not isinstance(result,dict) or result.get('success') is not True:
                raise LocalTrustApiError('friendship response envelope is invalid')
            if operation == 'friend.request' and isinstance(result.get('data'), dict):
                candidate = result['data'].get('id')
                if isinstance(candidate, str) and candidate:
                    event['response_resource_id'] = identifier(candidate, 'returned friendship request ID')
            event['status'] = 'ACKNOWLEDGED'
            self._save()
            return result
        except BaseException as error:
            self.invalidated = True
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            if isinstance(error,HTTPError):
                event['http_status'] = error.code
                error.close()
            self._save()
            if isinstance(error,KeyboardInterrupt): raise
            raise LocalTrustApiError('friendship mutation failed; retain exact journal without retry') from None

    def _assert(self, name, passed):
        self.state['assertions'].append({'id':self.case_id+'.'+name,'status':'PASS' if passed else 'FAIL'})
        self._save()
        if not passed: raise LocalTrustApiError('friendship business assertion did not pass')

    def _persisted(self, item, expected):
        self._active()
        sender,receiver=self._id(item['sender']),self._id(item['receiver'])
        database=OwnedSqliteDatabase(self.runtime)
        with database._connection() as db:
            db.execute('BEGIN')
            rows=db.execute('SELECT request_id,from_bot,to_bot,status FROM bcs_friend_requests '
                            'WHERE env=? AND request_id=? LIMIT 2',('local',item['request_id'])).fetchall()
            count=db.execute('SELECT COUNT(*) FROM bcs_friendships WHERE env=? AND '
                             '((left_bot=? AND right_bot=?) OR (left_bot=? AND right_bot=?))',
                             ('local',sender,receiver,receiver,sender)).fetchone()[0]
        self._active()
        self._assert(item['sender']+'-'+item['receiver']+'-'+expected+'-stored',
                     len(rows)==1 and dict(rows[0])=={'request_id':item['request_id'],
                         'from_bot':sender,'to_bot':receiver,'status':expected}
                     and count==(1 if expected=='accepted' else 0))

    def _friends(self, sender, receiver, expected):
        data=self._read(sender,'friends').get('data')
        ids=[row.get('bot_uuid') for row in data if isinstance(row,dict)] if isinstance(data,list) else []
        valid=(isinstance(data,list) and len(data)<100 and len(ids)==len(data)
               and all(isinstance(value,str) for value in ids) and len(set(ids))==len(ids))
        self._assert(sender+'-'+receiver+'-friends',valid and (self._id(receiver) in ids)==expected)

    def _request_match(self, item, status, direction):
        role = item['sender'] if direction == 'sent' else item['receiver']
        data = self._read(role,status+'-'+direction).get('data')
        matches = [row for row in data if isinstance(row,dict) and row.get('id')==item['request_id']] if isinstance(data,list) else []
        self._assert(item['sender']+'-'+item['receiver']+'-'+status+'-'+direction,
                     isinstance(data,list) and len(data)<100 and len(matches)==1
                     and matches[0].get('from_bot')==self._id(item['sender'])
                     and matches[0].get('to_bot')==self._id(item['receiver'])
                     and matches[0].get('status')==status)

    def _visibility_stored(self, role, expected, phase):
        self._active()
        database=OwnedSqliteDatabase(self.runtime)
        with database._connection() as db:
            rows=db.execute('SELECT bot_uuid,visibility,created_by,actor_kind,is_deleted FROM bcs_bots '
                            'WHERE env=? AND bot_uuid=? LIMIT 2',('local',self._id(role))).fetchall()
        self._active()
        suffix = '' if self.cli is not None and phase == 'final' else '-'+expected
        self._assert(role+'-'+phase+'-visibility-stored'+suffix,len(rows)==1 and dict(rows[0])=={
            'bot_uuid':self._id(role),'visibility':expected,'created_by':self.identities.human,
            'actor_kind':'bot','is_deleted':0})

    def _final_storage(self):
        self._active()
        ids=tuple(self._id(role) for role in ROLES)
        database=OwnedSqliteDatabase(self.runtime)
        with database._connection() as db:
            db.execute('BEGIN')
            requests=[dict(row) for row in db.execute('SELECT request_id,from_bot,to_bot,status FROM bcs_friend_requests '
                "WHERE env='local' AND (from_bot IN (?,?,?,?,?) OR to_bot IN (?,?,?,?,?))",ids+ids).fetchmany(len(self.pairs)+1)]
            friends=[tuple(row) for row in db.execute('SELECT left_bot,right_bot FROM bcs_friendships '
                "WHERE env='local' AND (left_bot IN (?,?,?,?,?) OR right_bot IN (?,?,?,?,?))",ids+ids).fetchmany(len(self.pairs))]
        self._active()
        expected=[{'request_id':item['request_id'],'from_bot':self._id(item['sender']),
                   'to_bot':self._id(item['receiver']),'status':item['status']} for item in self.state['requests']]
        expected_pairs={tuple(sorted((self._id(a),self._id(b)))) for a,b,status in self.pairs if status=='accepted'}
        self._assert('complete-owned-request-inventory',len(requests)==len(self.pairs) and sorted(requests,key=lambda r:r['request_id'])==sorted(expected,key=lambda r:r['request_id']))
        self._assert('complete-owned-friend-inventory',len(friends)==len(self.pairs)-1 and {tuple(sorted(pair)) for pair in friends}==expected_pairs)

    def _qualify(self, originals):
        required={'complete-owned-request-inventory','complete-owned-friend-inventory'}
        for sender,receiver,terminal in self.pairs:
            prefix=sender+'-'+receiver+'-'
            required.update(prefix+suffix for suffix in ('created',terminal+'-stored',terminal+'-sent',terminal+'-received','friends'))
            required.add(receiver+'-'+sender+'-friends')
            if receiver!='ENG':
                required.update(prefix+suffix for suffix in ('pending-stored','pending-sent','pending-received','pending-cleared'))
        for role in ROLES:
            required.update((role+'-final-original-visibility',role+'-final-visibility-stored-'+originals[role]))
        required.update(role+'-visibility-readback' for role in ('ENG','PM','CS'))
        if self.cli is not None:
            required.update(('cli-request-output', 'cli-received-exact-request', 'cli-accept-output', 'cli-ceo-list-includes-pm'))
        expected={self.case_id+'.'+name for name in required}
        if self.registration is not None:
            expected = set(self.registration.manifest['cases'][0]['assertions'])
        assertions=self.state['assertions'];ids=[item['id'] for item in assertions]
        self.state['required_assertions']=sorted(expected)
        self.state['expected_assertion_count']=len(expected)
        self.state['observed_assertion_count']=len(assertions)
        if self.registration is not None:
            self.state['requirement_results'] = self.registration.requirement_results(assertions)
        self._save()
        if (self.registration is not None and any(row['status']!='PASS' for row in self.state['requirement_results'])
                or not expected<=set(ids) or len(set(ids))!=len(ids)
                or any(item.get('status')!='PASS' for item in assertions)
                or len(self.state['requests'])!=len(self.pairs)
                or any(op['status']!='ACKNOWLEDGED' or op.get('resource_binding','BOUND')!='BOUND'
                       for op in self.state['operations'])
                or self.cli is not None and (len(self.state['cli_operations']) != 4 or any(
                    op['status']!='ACKNOWLEDGED' or op['resource_binding']!='BOUND'
                    or op['credential_cleanup']!='CLEARED' for op in self.state['cli_operations']))):
            raise LocalTrustApiError('friendship slice has missing, duplicate or nonpassing required evidence')

    def run(self):
        baseline=self.identities.trust_baseline()  # Always fresh, never caller-supplied evidence.
        self._active()
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        self.state={'schema_version':1,'contract':'owned-local-trust-api-cli-v1' if self.cli is not None else 'owned-local-trust-api-v1','case_id':self.case_id,
                    'run_id':self.runtime.run_id,'status':'RUNNING','functional':'NOT_RUN',
                    'visibility_restoration':'NOT_ATTEMPTED','baseline':baseline,
                    'fixture_digest':fingerprint(read(self.identities.fixtures.path)),
                    'operations':[],'requests':[],'assertions':[], 'cli_operations':[],
                    'resource_disposition':'RETAINED_LOCAL_DATABASE','automatic_relation_delete':False,
                    'full_story_status':'NOT_MIGRATED','cli_friendship':'NOT_TESTED','remote_execution':'NOT_TESTED',
                    'ready_to_execute':False,'started_at':int(time.time()),
                    'verification_scope':'LEGACY_API_AND_STORAGE_ONLY','edge_permission_graph':'NOT_VERIFIED',
                    'runtime_shutdown':'NOT_YET_OBSERVED',
                    'notification_delivery':'NOT_VERIFIED','restoration_atomic':False}
        if self.registration is not None:
            self.state.update(self.registration.binding())
            self.state.update(self.registration.context.binding())
        self._save()
        originals={row['role']:row['original_visibility'] for row in baseline['roles']}
        try:
            from .trust_api_steps import execute_trust_steps
            execute_trust_steps(self, originals)
            self._qualify(originals)
            self.state.update(status='LOCAL_TRUST_API_PASS',visibility_restoration='PASS')
            if self.cli is not None:
                self.state.update(status='LOCAL_TRUST_API_CLI_PASS', cli_friendship='PASS',
                                  verification_scope='LEGACY_API_CLI_AND_STORAGE',
                                  local_parent_business='PASS', remote_execution='NOT_TESTED')
            self._save()
            return self.state
        except BaseException as error:
            self.invalidated=True
            cancelled=has_cancellation(error)
            shutdown_unconfirmed = getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED' or any(
                op.get('child_shutdown_status') == 'UNCONFIRMED' for op in self.state['cli_operations'])
            if shutdown_unconfirmed:
                self.state['child_shutdown_status'] = 'UNCONFIRMED'
            self.state.update(status='CANCELLED' if cancelled else 'BLOCKED',
                              error_type=type(primary_error(error)).__name__,visibility_restoration='UNCONFIRMED')
            if self.state['functional']!='PASS':self.state['functional']='FAIL'
            try:
                if self.journal_integrity_lost:
                    self._integrity_outcome(cancelled)
                else:
                    self._save()
            except BaseException as publication:
                cancelled=cancelled or has_cancellation(publication)
                failure = (KeyboardInterrupt('friendship cancelled; journal finalization failed') if cancelled else
                           LocalTrustApiError('friendship failed; journal finalization failed, retain runtime artifacts'))
                if shutdown_unconfirmed:
                    failure.child_shutdown_status = 'UNCONFIRMED'
                raise failure from None
            finally:
                self.identities.close()
            failure = KeyboardInterrupt() if cancelled else LocalTrustApiError(
                'friendship slice did not qualify; inspect the retained private journal')
            if shutdown_unconfirmed:
                failure.child_shutdown_status = 'UNCONFIRMED'
            raise failure from None
