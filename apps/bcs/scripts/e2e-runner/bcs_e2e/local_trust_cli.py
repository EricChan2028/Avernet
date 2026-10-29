"""Finite product CLI friendship operations using fresh owned-local Bot contexts."""
from __future__ import annotations

import json
import stat
from pathlib import Path
import uuid

from .bounded_process import capture
from .errors import has_cancellation
from .local_cli import require_cli_binary
from .local_cli_exchange import LocalCliExchange
from .profile import unique_keys
from .run_state import read, write
from .trust_baseline import visibility


class LocalTrustCliError(RuntimeError):
    pass


class OwnedLocalTrustCli:
    def __init__(self, binary: Path, binary_sha256: str):
        require_cli_binary(binary, binary_sha256)
        self.binary, self.digest = binary, binary_sha256

    def execute(self, case, operation: str, *, request_id: str = ''):
        case._active()
        require_cli_binary(self.binary, self.digest)
        if operation not in ('request', 'requests', 'accept', 'list'):
            raise LocalTrustCliError('unsupported product CLI friendship operation')
        previous = [op['operation'] for op in case.state['cli_operations']]
        if previous != list(('request', 'requests', 'accept', 'list')[:len(previous)]) or len(previous) >= 4 or operation != ('request', 'requests', 'accept', 'list')[len(previous)]:
            raise LocalTrustCliError('CLI friendship operations cannot be reordered or replayed')
        role = 'CEO' if operation in ('request', 'list') else 'PM'
        entry = case.identities._entries[role]
        bot_id, token = entry
        # The native friend endpoints otherwise permit local Human fallback.
        visibility(case._read(role, 'visibility'), bot_id)
        friends = case._read(role, 'friends').get('data')
        if not isinstance(friends, list) or len(friends) >= 100:
            raise LocalTrustCliError('Bot self identity read is incomplete')
        visibility(case._read(role, 'visibility'), bot_id)
        if case.identities._entries[role] != (bot_id, token):
            raise LocalTrustCliError('CLI Bot credential changed during admission')
        peer = case._id('PM')
        known = next((row for row in case.state['requests'] if row['sender']=='CEO' and row['receiver']=='PM'), None)
        if operation in ('requests', 'accept', 'list') and (known is None or known['request_id'] != request_id
                or known['status'] != ('accepted' if operation=='list' else 'pending')):
            raise LocalTrustCliError('CLI requires the exact freshly bound CEO to PM request')
        if operation == 'request':
            if known is not None or request_id:
                raise LocalTrustCliError('CLI request cannot adopt prior history')
            method, path, body = 'POST', '/friends/request', {'to_bot': peer}
            arguments = ['friend', 'request', '--bot-uuid', peer]
        elif operation == 'requests':
            method, path, body = 'GET', '/friends/requests?direction=received&status=pending', None
            arguments = ['friend', 'requests', '--direction', 'received', '--status', 'pending']
        elif operation == 'accept':
            from .profile import identifier
            identifier(request_id, 'CLI friend request ID')
            method, path, body = 'POST', '/friends/requests/'+request_id+'/accept', None
            arguments = ['friend', 'accept', '--request-id', request_id]
        else:
            method, path, body = 'GET', '/bots/'+bot_id+'/friends', None
            arguments = ['friend', 'list']  # Resolve self from the product context, not a caller flag.
        root = case.runtime.root / ('trust-cli-'+uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        for name in ('home', 'bot', 'tmp'):
            (root/name).mkdir(mode=0o700)
        (root/'bot'/'.bcs').mkdir(mode=0o700)
        context = root/'bot'/'.bcs'/'session.json'
        directories = {path:(path.stat().st_dev,path.stat().st_ino) for path in (root,root/'bot',context.parent)}
        def require_context():
            for path,identity in directories.items():
                current = path.lstat()
                if not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino) != identity:
                    raise LocalTrustCliError('owned CLI context directory changed')
            if not context.exists() or read(context) != value:
                raise LocalTrustCliError('owned CLI credential context changed')
        event = {'operation':operation, 'role':role, 'request_id':request_id,
                 'method':method, 'path':path, 'status':'INTENDED',
                 'submitted_request_id':'e2e-cli-'+uuid.uuid4().hex,
                 'dispatch_status':'NOT_DISPATCHED', 'credential_cleanup':'NOT_YET_OBSERVED',
                 'binary_sha256':self.digest, 'context_dir':str(root),
                 'transport':'OWNED_LOOPBACK_CAPTURE', 'resource_binding':'UNKNOWN' if operation=='request' else 'BOUND'}
        case.state['cli_operations'].append(event)
        case._save()
        credential_written = False
        try:
            with LocalCliExchange(case, event, token=token, body=body) as exchange:
                value = {'schema_version':1, 'token':token, 'bot_uuid':bot_id, 'api_base_url':exchange.origin}
                credential_written = True  # Write failure still requires cleanup of a partial credential.
                write(context, value)
                environment = {'PATH':'/usr/bin:/bin', 'HOME':str(root/'home'),
                    'BOT_DATA_DIR':str(root/'bot'), 'TMPDIR':str(root/'tmp'), 'LANG':'C',
                    'SERVER_ENV':'local', 'tc_sdb_nenv':'production'}
                case._active()
                require_cli_binary(self.binary, self.digest)
                require_context()
                result = capture([str(self.binary), '--json', '--log-level', 'error', *arguments],
                                 cwd=root, environment=environment, timeout_seconds=12, max_output_bytes=65_536)
            require_context()
            event['cli_exit_code'] = result.returncode
            case._active()
            require_cli_binary(self.binary, self.digest)
            if (exchange.failure is not None or exchange.attempts != 1 or exchange.forwarded != 1
                    or result.returncode != 0 or event.get('http_status') != (201 if operation=='request' else 200)
                    or not isinstance(exchange.response, dict) or exchange.response.get('success') is not True):
                raise LocalTrustCliError('CLI wire operation was not exactly confirmed')
            output = json.loads(result.stdout.decode('utf-8'), object_pairs_hook=unique_keys)
            expected = ({'success':True} if operation=='accept' else
                        {'success':True, 'data':exchange.response.get('data'), 'message':exchange.response.get('message')}
                        if operation=='request' else exchange.response.get('data'))
            if json.dumps(output,sort_keys=True,allow_nan=False) != json.dumps(expected,sort_keys=True,allow_nan=False):
                raise LocalTrustCliError('CLI JSON differs from its actual wire response')
            event['status'] = 'ACKNOWLEDGED'
            return output
        except BaseException as error:
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                event['child_shutdown_status'] = 'UNCONFIRMED'
            case.invalidated = True
            failure = KeyboardInterrupt() if has_cancellation(error) else LocalTrustCliError(
                'CLI friendship outcome unconfirmed; no retry or restoration')
            if event.get('child_shutdown_status') == 'UNCONFIRMED':
                failure.child_shutdown_status = 'UNCONFIRMED'
            raise failure from None
        finally:
            try:
                if credential_written:
                    require_context()
                    write(context, {'schema_version':1, 'token':'', 'api_base_url':case.identities.origin})
                    if read(context).get('token') != '':
                        raise LocalTrustCliError('CLI credential clearing was not confirmed')
                event['credential_cleanup'] = 'CLEARED' if credential_written else 'NOT_WRITTEN'
            except BaseException:
                case.invalidated = True
                event['credential_cleanup'] = 'UNCONFIRMED'
                raise
            finally:
                case._save()
