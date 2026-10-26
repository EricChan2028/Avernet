"""Complete original bcs-cli Channel parent in one owned local BCS lifecycle."""
from __future__ import annotations

import copy
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import time
import uuid
from urllib.parse import quote, urlencode, urlsplit

from .inventory import current_manifest, executor_digest
from .ledger import Ledger, LedgerError
from .local_channel_cli_contract import CASE_ID, SUITE, REQUIREMENTS, channel_cli_source_binding
from .local_cli import require_cli_binary
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write
from .trust_baseline import visibility

ROLES=('CEO','PM','ENG','QA')
BINDING_NAME='channel-cli-binding.json'; JOURNAL_NAME='local-channel-cli.json'
LEDGER_NAME='channel-cli-resources.sqlite'
FULL_OPERATIONS=(
    'channel-bind','channel-list-before','eng-visibility-before','eng-visibility-public',
    'eng-visibility-public-read','group-create','session-create','channel-conversation-empty',
    'channel-unbind','channel-list-after','session-delete','group-delete',
    'eng-visibility-restore','eng-visibility-restored-read')
CLI_OPERATIONS=('channel-bind','channel-list-before','group-create','session-create',
                'channel-conversation-empty','channel-unbind','channel-list-after')
REQ_BY_OPERATION=dict(zip(
    ('channel-bind','channel-list-before','group-create','session-create','channel-conversation-empty',
     'channel-unbind','channel-list-after','session-delete','group-delete'), REQUIREMENTS))


class LocalChannelCliError(RunStateError): pass

def _copy(value): return json.loads(json.dumps(value,allow_nan=False))


class OwnedLocalChannelCli:
    ROLES = ROLES
    def __init__(self, identities, binary:Path, binary_sha256:str):
        if (type(identities) is not OwnedLocalRoleIdentity or identities.fixture_set!='workspace'
                or tuple(identities.roles)!=ROLES or set(identities._verified)!=set(ROLES)):
            raise LocalChannelCliError('Channel CLI requires freshly verified CEO/PM/ENG/QA identities')
        identities._require_roles(ROLES)
        if type(identities.runtime) is not OwnedBcsProcess or identities.runtime.provider_sink is not None:
            raise LocalChannelCliError('Channel CLI requires one provider-free owned BCS')
        if identities.runtime.store_messages is not False:
            raise LocalChannelCliError('Channel CLI requires store_messages=False')
        require_cli_binary(binary,binary_sha256)
        self.identities=identities;self.runtime=identities.runtime;self.root=self.runtime.root
        self.binary=binary.resolve();self.binary_sha256=binary_sha256
        self.path=self.root/JOURNAL_NAME;self.ledger_path=self.root/LEDGER_NAME
        self.binding_path=self.root/BINDING_NAME;self.executor_digest=executor_digest()
        self.source_binding=channel_cli_source_binding();self.binding=self._binding()
        self.binding_digest=fingerprint(self.binding);self.fixture_digest=fingerprint(read(identities.fixtures.path))
        self.config_digest=file_sha256(self.runtime.inputs.config_path)
        parsed=urlsplit(identities.origin)
        if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port
                or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username):
            raise LocalChannelCliError('Channel CLI requires an owned loopback origin')
        self.port=parsed.port;self.missing='missing-channel-cli-'+hashlib.sha256(self.runtime.run_id.encode()).hexdigest()[:24]
        self.account='cli-channel-'+hashlib.sha256((self.runtime.run_id+'-account').encode()).hexdigest()[:16]
        self.state=None;self.ledger=None;self.journal_digest='';self.invalidated=False
        if self.path.exists() or self.path.is_symlink() or self.ledger_path.exists() or self.ledger_path.is_symlink():
            raise LocalChannelCliError('Channel CLI requires fresh journal and Ledger paths')
        self._active()

    def _binding(self):
        keys={'schema_version','contract','suite','case_id','run_id','executor_digest','bcs_binary_sha256',
              'cli_binary_sha256','source_binding','selected_requirement_ids','legacy_inventory','runtime_dir','store_messages'}
        try:
            value=read(self.binding_path)
            if (self.binding_path.is_symlink() or set(value)!=keys or value['schema_version']!=1
                    or value['contract']!=SUITE or value['suite']!=SUITE or value['case_id']!=CASE_ID
                    or value['run_id']!=self.runtime.run_id or value['executor_digest']!=self.executor_digest
                    or value['bcs_binary_sha256']!=self.runtime.binary_sha256
                    or value['cli_binary_sha256']!=self.binary_sha256
                    or value['source_binding']!=self.source_binding
                    or value['selected_requirement_ids']!=list(REQUIREMENTS)
                    or value['legacy_inventory']!=current_manifest()['legacy_inventory']
                    or value['runtime_dir']!=str(self.root.resolve()) or value['store_messages'] is not False):
                raise ValueError
            return value
        except Exception:
            raise LocalChannelCliError('frozen Channel CLI binding is missing or changed') from None

    def _active(self):
        if self.invalidated: raise LocalChannelCliError('Channel CLI mutation authority is invalidated')
        self.identities._require_roles(ROLES);require_cli_binary(self.binary,self.binary_sha256)
        config=json.loads(self.runtime.inputs.config_path.read_text())
        if (self.runtime.state!='RUNNING' or not self.runtime.ready or self.runtime.child is None
                or self.runtime.child.poll() is not None or not self.runtime._owns_listener(1)
                or executor_digest()!=self.executor_digest or file_sha256(self.runtime.inputs.config_path)!=self.config_digest
                or fingerprint(read(self.identities.fixtures.path))!=self.fixture_digest
                or fingerprint(channel_cli_source_binding())!=fingerprint(self.source_binding)
                or fingerprint(self._binding())!=self.binding_digest
                or config.get('channel',{})!={} or config.get('store_messages') is not False
                or config.get('llm')!={'type':'none'} or config.get('eventing')!={'enabled':False,'dispatcher_enabled':False}):
            raise LocalChannelCliError('Channel CLI runtime, source, fixture, or disabled-channel binding changed')
        if self.journal_digest and fingerprint(read(self.path))!=self.journal_digest:
            self.invalidated=True;raise LocalChannelCliError('Channel CLI journal changed')

    def _save(self):
        if self.journal_digest and fingerprint(read(self.path))!=self.journal_digest:
            self.invalidated=True;raise LocalChannelCliError('Channel CLI journal changed')
        encoded=json.dumps(self.state,sort_keys=True,separators=(',',':'),allow_nan=False)
        secrets=[token for _,token in self.identities._entries.values() if token]
        if any(secret in encoded for secret in secrets):
            self.invalidated=True;raise LocalChannelCliError('Channel CLI evidence contains a Bot credential')
        write(self.path,self.state);self.journal_digest=fingerprint(self.state)

    @staticmethod
    def _exclusive(path):
        try: fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError: raise LocalChannelCliError('fresh Channel CLI journal required') from None
        os.close(fd)

    def _id(self,role): self._active();return self.identities._entries[role][0]
    def _ledger_row(self,handle):
        try:return dict(self.ledger._get(handle))
        except (LedgerError,AttributeError):raise LocalChannelCliError('Channel CLI Ledger handle missing') from None
    def _owned(self,handle,kind,resource_id,parent=None,states=('live','deleted')):
        row=self._ledger_row(handle)
        if (row['kind']!=kind or row['case_id']!=CASE_ID or row['attempt']!=1 or row['origin']!='created'
                or row['resource_id']!=resource_id or row['parent']!=parent or row['state'] not in states):
            raise LocalChannelCliError('resource is not the exact owned Channel CLI binding')
        return row
    def _group(self):
        value=self.state['resources']['group'];self._owned(value['handle'],'group',value['id'])
        self._owned(value['initial_session_handle'],'session',value['initial_session_id'],value['handle'])
        return value
    def _session(self):
        value=self.state['resources']['session'];group=self._group()
        self._owned(value['handle'],'session',value['id'],group['handle']);return value

    def _spec(self,operation,secret=''):
        ceo,pm,eng=self._id('CEO'),self._id('PM'),self._id('ENG')
        if operation=='channel-bind':
            if (not isinstance(secret,str) or not secret.startswith('synthetic-')
                    or not secret.endswith('-channel-secret') or len(secret)!=57):
                raise LocalChannelCliError('Channel bind requires one fresh in-memory synthetic secret')
            body={'channel_type':'ding_talk','account_ref':self.account,'target':{'bot':{'bot_id':ceo}},
              'outbound_visibility':'full_transcript','env':'local','config':{'channel_type':'ding_talk',
              'robot_code':'e2e-robot','client_id':'e2e-client','client_secret':secret,
              'send_mode':{'mode':'normal','message_type':'markdown'}}}
            args=['channel','bind','--account',self.account,'--target-kind','bot','--target-id',ceo,
              '--visibility','full_transcript','--env','local','--robot-code','e2e-robot',
              '--client-id','e2e-client','--client-secret',secret,'--send-mode','normal','--message-type','markdown']
            return ('CEO','POST','/channels/bindings',body,args,400,'bind-redacted-error')
        if operation in ('channel-list-before','channel-list-after'):
            return ('CEO','GET','/channels/bindings',None,['channel','list'],200,'json')
        if operation.startswith('eng-visibility'):
            if operation in ('eng-visibility-before','eng-visibility-public-read','eng-visibility-restored-read'):
                return ('ENG','GET','/bots/'+quote(eng,safe='')+'/visibility',None,None,200,'HTTP')
            expected='public' if operation=='eng-visibility-public' else 'protected'
            return ('ENG','PUT','/bots/'+quote(eng,safe='')+'/visibility',{'visibility':expected},None,200,'HTTP')
        if operation=='group-create':
            body={'id':None,'label':None,'driver_bot':pm,'participants':[{'bot_uuid':pm,'role':None},{'bot_uuid':eng,'role':None}],
              'context':'Release coordination through bcs-cli','topic':'CLI channel conversation lookup','create_initial_session':True}
            args=['create-group','--driver',pm,'--participants',pm+','+eng,'--context','Release coordination through bcs-cli',
                  '--topic','CLI channel conversation lookup']
            return ('PM','POST','/groups',body,args,200,'group-create-human')
        group=self._group();gid=quote(group['id'],safe='')
        if operation=='session-create':
            return ('PM','POST','/groups/'+gid+'/sessions',{'session_title':'CLI channel lookup'},
                    ['session','create','--group',group['id'],'--title','CLI channel lookup'],201,'json')
        session=self._session();sid=quote(session['id'],safe='')
        if operation=='channel-conversation-empty':
            path='/channels/conversations/by-session?bcs_session_id='+sid+'&channel_type=dingtalk'
            return ('PM','GET',path,None,['channel','conversation-id','--session',session['id']],200,'json')
        if operation=='channel-unbind':
            return ('CEO','DELETE','/channels/bindings/'+self.missing,None,['channel','unbind','--id',self.missing],200,'json')
        if operation=='session-delete':
            return ('PM','DELETE','/sessions/'+sid+'?'+urlencode({'bot_id':pm}),None,None,200,'HTTP')
        if operation=='group-delete':
            return ('PM','DELETE','/groups/'+gid+'?'+urlencode({'bot_id':pm}),None,None,200,'HTTP')
        raise LocalChannelCliError('operation lacks a finite Channel CLI request contract')

    def _append_operation(self,operation,method,path,role,request_id,*,transport,handles=()):
        self._active();index=len(self.state['operations'])
        if index>=len(FULL_OPERATIONS) or FULL_OPERATIONS[index]!=operation:
            raise LocalChannelCliError('Channel CLI operations cannot be reordered or replayed')
        row={'operation':operation,'method':method,'path':path,'role':role,'http_status':None,
             'submitted_request_id':request_id,'status':'INTENDED','transitions':[{'status':'INTENDED'}],
             'transport':transport,'dispatch_status':'NOT_DISPATCHED'}
        if handles:row.update(resource_handles=list(handles),resource_binding='UNKNOWN')
        self.state['operations'].append(row);self._save();return row

    def _admit_cli(self,operation,role,method,path,body,args,status,kind,handles,secret):
        self._active();expected=self._spec(operation,secret)
        if operation not in CLI_OPERATIONS or expected!=(role,method,path,body,args,status,kind):
            raise LocalChannelCliError('CLI operation differs from its finite request and argument contract')
        if operation=='group-create':
            if len(handles)!=2:raise LocalChannelCliError('Group create requires Group and implicit Session intents')
            a,b=(self._ledger_row(h) for h in handles)
            if a['kind']!='group' or a['parent'] is not None or b['kind']!='session' or b['parent']!=handles[0]:
                raise LocalChannelCliError('Group create intent kinds or parent differ')
        elif operation=='session-create':
            if len(handles)!=1:raise LocalChannelCliError('Session create requires one child intent')
            if self._ledger_row(handles[0])['parent']!=self._group()['handle']:
                raise LocalChannelCliError('Session create intent parent differs')
        elif handles:raise LocalChannelCliError('non-create CLI operation cannot bind a resource intent')
        if operation=='channel-unbind':
            admissions=[row for row in self.state.get('checks',[]) if row.get('id')=='unbind-empty-admission']
            evidence=admissions[0].get('evidence',{}) if len(admissions)==1 else {}
            counts=evidence.get('row_counts',{}) if isinstance(evidence,dict) else {}
            if (len(admissions)!=1 or admissions[0].get('status')!='PASS' or evidence.get('status')!='PASS'
                    or evidence.get('source')!='OWNED_LOCAL_SQLITE' or set(counts)!={
                    'bcs_channel_bindings','bcs_channel_conversations','bcs_channel_im_participants'}
                    or any(counts.values())):
                raise LocalChannelCliError('missing Channel unbind lacks durable empty-table admission')
        for handle in handles:
            row=self._ledger_row(handle)
            if (row['case_id']!=CASE_ID or row['attempt']!=1 or row['origin']!='unknown'
                    or row['state']!='intended' or row['resource_id'] is not None):
                raise LocalChannelCliError('CLI create intent is borrowed, repeated, or already bound')


    def _require_delete_admission(self,operation):
        admissions=[row for row in self.state.get('checks',[]) if row.get('id')=='cleanup-admission']
        evidence=admissions[0].get('evidence',{}) if len(admissions)==1 else {}
        if (len(admissions)!=1 or admissions[0].get('status')!='PASS'
                or evidence.get('status')!='LOCAL_DELETION_ADMITTED'
                or evidence.get('contract')!='owned-local-foreground-transport-only-v1'
                or evidence.get('engine')!='NOT_STARTED' or evidence.get('provider_bindings')!=0
                or evidence.get('queued_deliveries')!=0 or evidence.get('state_machine_runs')!=0):
            raise LocalChannelCliError('resource delete lacks durable owned-local admission')
        group=self.state.get('resources',{}).get('group') or {}
        session=self.state.get('resources',{}).get('session') or {}
        if operation=='session-delete':
            self._owned(group.get('handle'),'group',group.get('id'),states=('live',))
            self._owned(group.get('initial_session_handle'),'session',group.get('initial_session_id'),group.get('handle'),('live',))
            self._owned(session.get('handle'),'session',session.get('id'),group.get('handle'),('live',))
        elif operation=='group-delete':
            self._owned(group.get('handle'),'group',group.get('id'),states=('live',))
            self._owned(group.get('initial_session_handle'),'session',group.get('initial_session_id'),group.get('handle'),('live',))
            self._owned(session.get('handle'),'session',session.get('id'),group.get('handle'),('deleted',))
        else:
            raise LocalChannelCliError('unreviewed destructive Channel CLI operation')

    def _operation_failed(self,event,error,*,handles=(),mutation=False):
        event['status']='UNKNOWN';event['transitions'].append({'status':'UNKNOWN','error_type':type(error).__name__})
        if getattr(error,'child_shutdown_status',None)=='UNCONFIRMED':event['child_shutdown_status']='UNCONFIRMED'
        if handles:
            for handle in handles:
                try:self.ledger.unknown(handle)
                except BaseException:pass
        if mutation:self.invalidated=True
        try:self._save()
        except BaseException:pass

    def _http(self,operation):
        self._active();role,method,path,body,args,expected,kind=self._spec(operation)
        if args is not None or kind!='HTTP':raise LocalChannelCliError('HTTP operation contract differs')
        if operation in ('session-delete','group-delete'):self._require_delete_admission(operation)
        request_id='e2e-channel-cli-http-'+uuid.uuid4().hex
        event=self._append_operation(operation,method,path,role,request_id,transport='LEGACY_HTTP')
        token=self.identities._entries[role][1];headers={'Accept':'application/json','X-Request-ID':request_id}
        if operation.startswith('eng-visibility'):headers['Authorization']='Bearer '+token
        raw=json.dumps(body,separators=(',',':')).encode() if body is not None else None
        if raw is not None:headers['Content-Type']='application/json'
        connection=HTTPConnection('127.0.0.1',self.port,timeout=5)
        try:
            deadline=time.monotonic()+5
            connection.request(method,path,body=raw,headers=headers);sock=connection.sock
            response=connection.getresponse();payload=bytearray()
            with response:
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise LocalChannelCliError('Channel CLI HTTP response deadline exceeded')
                    if sock is not None:sock.settimeout(remaining)
                    chunk=response.read1(min(4096,65_537-len(payload)))
                    if not chunk:break
                    payload.extend(chunk)
                    if len(payload)>65_536:raise LocalChannelCliError('Channel CLI HTTP response exceeds its budget')
                if response.length not in (None,0):raise LocalChannelCliError('Channel CLI HTTP response was truncated')
                code=response.status
            value=json.loads(bytes(payload).decode(),object_pairs_hook=unique_keys)
            if code!=expected or not isinstance(value,dict):raise LocalChannelCliError('Channel CLI HTTP response differs')
            event.update(http_status=code,status='ACKNOWLEDGED',dispatch_status='RESPONSE_RECEIVED')
            event['transitions'].append({'status':'ACKNOWLEDGED','http_status':code});self._save();return value
        except BaseException as error:
            self._operation_failed(event,error,mutation=method in ('POST','PUT','PATCH','DELETE'));raise
        finally:connection.close();token=''

    def _pass(self,operation,evidence):
        requirement=REQ_BY_OPERATION[operation]
        expected=list(REQUIREMENTS[:len(self.state['assertions'])+1])
        if expected[-1]!=requirement:raise LocalChannelCliError('Channel CLI assertions are out of order')
        self.state['assertions'].append({'id':requirement,'status':'PASS'})
        self.state['checks'].append({'id':requirement,'status':'PASS','evidence':evidence});self._save()

    def _visibility(self,operation,expected):
        value=self._http(operation);observed=visibility(value,self._id('ENG'))
        if observed!=expected:raise LocalChannelCliError('ENG visibility proof differs')
        return observed

    def run(self):
        from .local_channel_cli_driver import ChannelCliDriver
        from .local_channel_cli_storage import (channel_empty_snapshot,deletion_admission,
          explicit_session_deletion_proof,fixture_snapshot,group_deletion_proof,history_snapshot,
          owned_snapshot,unrelated_snapshot)
        self._active();self._exclusive(self.path)
        self.state={'schema_version':1,'contract':SUITE,'suite':SUITE,'case_id':CASE_ID,
          'run_id':self.runtime.run_id,'status':'RUNNING','executor_digest':self.executor_digest,
          'binding_digest':self.binding_digest,'source_binding_digest':fingerprint(self.source_binding),
          'assertions':[],'operations':[],'resources':{'group':None,'session':None},'checks':[],
          'preservation':{'fixtures':{'preserved':False},'unrelated':{'preserved':False}},
          'visibility':{},'cli':{'operation_count':0,'credential_contexts_cleared':False},
          'execution_scope':{'runtime':'OWNED_LOCAL_FOREGROUND','store_messages':False,
            'channel_bridge':'DISABLED','engine':'NOT_STARTED','provider':'NOT_CONFIGURED',
            'receiver':'NOT_OPENED','queue':'NOT_STARTED','state_machine':'NOT_STARTED',
            'business_execution':'NOT_CLAIMED'},'missing_binding_id':self.missing}
        self._save()
        try:
            with Ledger(self.ledger_path,self.runtime.run_id,self.binding_digest,create=True) as ledger:
                self.ledger=ledger;fixtures_before=fixture_snapshot(self);unrelated_before=unrelated_snapshot(self)
                initial_channels=channel_empty_snapshot(self)
                self.state['preservation']['fixtures']['before']=fixtures_before
                self.state['preservation']['unrelated']['before']=unrelated_before
                self.state['checks'].append({'id':'channel-empty-before','status':'PASS','evidence':initial_channels});self._save()
                cli=ChannelCliDriver(self,self.binary,self.binary_sha256);cli.require_tokens()
                secret='synthetic-'+uuid.uuid4().hex+'-channel-secret'
                role,method,path,body,args,status,kind=self._spec('channel-bind',secret)
                reply=cli.invoke(operation='channel-bind',role=role,method=method,path=path,body=body,
                    arguments=args,expected_status=status,output_kind=kind,
                    request_id='e2e-channel-cli-bind-'+uuid.uuid4().hex,secret=secret)
                secret='';self._pass('channel-bind',{'http_status':400,'code':reply.get('code'),
                    'disabled_bridge':True,'secret_redacted_stdout_stderr':True})
                role,method,path,body,args,status,kind=self._spec('channel-list-before')
                reply=cli.invoke(operation='channel-list-before',role=role,method=method,path=path,body=body,
                    arguments=args,expected_status=status,output_kind=kind,request_id='e2e-channel-cli-list-'+uuid.uuid4().hex)
                if reply!={'items':[]}:raise LocalChannelCliError('initial Channel list is not empty')
                self._pass('channel-list-before',{'items':[]})
                self.state['visibility']['before']=self._visibility('eng-visibility-before','protected')
                self.state['visibility']['public_write']=self._visibility('eng-visibility-public','public')
                self.state['visibility']['public_read']=self._visibility('eng-visibility-public-read','public')
                request_id='e2e-channel-cli-group-'+uuid.uuid4().hex
                handles=self.ledger.group_creation_intent(CASE_ID,1,request_id)
                role,method,path,body,args,status,kind=self._spec('group-create')
                reply=cli.invoke(operation='group-create',role=role,method=method,path=path,body=body,
                    arguments=args,expected_status=status,output_kind=kind,handles=handles,request_id=request_id)
                try:
                    gid=identifier(reply.get('id'),'Channel CLI Group ID');initial=identifier(reply.get('session_id'),'Channel CLI implicit Session ID')
                    if not initial.startswith(gid+':'):raise ValueError
                    self.ledger.observed(handles[0],gid,origin='created',actual_request_id=request_id)
                    self.ledger.observed(handles[1],initial,origin='created')
                    self.state['resources']['group']={'id':gid,'handle':handles[0],'initial_session_id':initial,
                        'initial_session_handle':handles[1],'attempt':1}
                    self.state['operations'][-1].update(resource_binding='BOUND',candidate_ids={'group':gid,'initial_session':initial});self._save()
                except BaseException:
                    for handle in handles:
                        try:self.ledger.unknown(handle)
                        except BaseException:pass
                    self.invalidated=True;raise LocalChannelCliError('Group response could not be bound') from None
                self._pass('group-create',{'group_id':gid,'initial_session_id':initial})
                request_id='e2e-channel-cli-session-'+uuid.uuid4().hex
                handle=self.ledger.intent(CASE_ID,1,'session',request_id,handles[0])
                role,method,path,body,args,status,kind=self._spec('session-create')
                reply=cli.invoke(operation='session-create',role=role,method=method,path=path,body=body,
                    arguments=args,expected_status=status,output_kind=kind,handles=(handle,),request_id=request_id)
                try:
                    sid=identifier(reply.get('session_id'),'Channel CLI explicit Session ID')
                    if reply.get('id')!=sid or reply.get('group_id')!=gid or sid==initial:raise ValueError
                    self.ledger.observed(handle,sid,origin='created',actual_request_id=request_id)
                    self.state['resources']['session']={'id':sid,'handle':handle,'group_id':gid,'attempt':1}
                    self.state['operations'][-1].update(resource_binding='BOUND',candidate_ids={'session':sid});self._save()
                except BaseException:
                    try:self.ledger.unknown(handle)
                    except BaseException:pass
                    self.invalidated=True;raise LocalChannelCliError('Session response could not be bound') from None
                self._pass('session-create',{'session_id':sid})
                role,method,path,body,args,status,kind=self._spec('channel-conversation-empty')
                reply=cli.invoke(operation='channel-conversation-empty',role=role,method=method,path=path,body=body,
                    arguments=args,expected_status=status,output_kind=kind,request_id='e2e-channel-cli-conversation-'+uuid.uuid4().hex)
                if reply!={'items':[]}:raise LocalChannelCliError('Channel conversation lookup is not empty')
                self._pass('channel-conversation-empty',{'items':[]})
                before_unbind=channel_empty_snapshot(self)
                if before_unbind!=initial_channels:raise LocalChannelCliError('Channel tables changed before missing unbind admission')
                self.state['checks'].append({'id':'unbind-empty-admission','status':'PASS','evidence':before_unbind});self._save()
                role,method,path,body,args,status,kind=self._spec('channel-unbind')
                reply=cli.invoke(operation='channel-unbind',role=role,method=method,path=path,body=body,
                    arguments=args,expected_status=status,output_kind=kind,request_id='e2e-channel-cli-unbind-'+uuid.uuid4().hex)
                if reply!={'ok':True}:raise LocalChannelCliError('missing Channel unbind acknowledgement differs')
                self._pass('channel-unbind',{'ok':True,'binding_id':self.missing,'admission':'PROVED_ABSENT'})
                role,method,path,body,args,status,kind=self._spec('channel-list-after')
                reply=cli.invoke(operation='channel-list-after',role=role,method=method,path=path,body=body,
                    arguments=args,expected_status=status,output_kind=kind,request_id='e2e-channel-cli-list-'+uuid.uuid4().hex)
                if reply!={'items':[]}:raise LocalChannelCliError('final Channel list is not empty')
                final_channels=channel_empty_snapshot(self)
                if final_channels!=initial_channels:raise LocalChannelCliError('Channel tables changed after CLI operations')
                self._pass('channel-list-after',{'items':[],'sqlite':final_channels})
                owned=owned_snapshot(self);history=history_snapshot(self);admission=deletion_admission(self)
                self.state['checks'].extend(({'id':'owned-resource-snapshot','status':'PASS','evidence':owned},
                    {'id':'cleanup-admission','status':'PASS','evidence':admission},
                    {'id':'retained-history-before','status':'PASS','evidence':history}));self._save()
                deleted=self._http('session-delete')
                if deleted!={'deleted':True,'session_id':sid}:raise LocalChannelCliError('Session cleanup acknowledgement differs')
                session_proof=explicit_session_deletion_proof(self);self.ledger.mark_deleted(handle)
                self.state['checks'].append({'id':'session-delete-proof','status':'PASS','evidence':session_proof})
                self._pass('session-delete',session_proof)
                deleted=self._http('group-delete')
                if deleted!={'deleted':True,'id':gid}:raise LocalChannelCliError('Group cleanup acknowledgement differs')
                group_proof=group_deletion_proof(self,history)
                self.ledger.mark_deleted(handles[1]);self.ledger.mark_deleted(handles[0])
                self.state['checks'].append({'id':'group-delete-proof','status':'PASS','evidence':group_proof})
                self._pass('group-delete',group_proof)
                self.state['visibility']['restore_write']=self._visibility('eng-visibility-restore','protected')
                self.state['visibility']['restored_read']=self._visibility('eng-visibility-restored-read','protected')
                fixtures_after=fixture_snapshot(self);unrelated_after=unrelated_snapshot(self,gid)
                if fixtures_after!=fixtures_before or unrelated_after!=unrelated_before:
                    raise LocalChannelCliError('Channel CLI fixtures or unrelated local state changed')
                self.state['preservation']['fixtures'].update(after=fixtures_after,preserved=True)
                self.state['preservation']['unrelated'].update(after=unrelated_after,preserved=True)
                rows=[{k:r[k] for k in ('handle','case_id','attempt','kind','parent','request_id','origin','resource_id','state')}
                      for r in self.ledger.resource_snapshot()]
                self.state['resources']['ledger_summary']={'resource_count':len(rows),
                    'deleted_count':sum(r['state']=='deleted' for r in rows),'resources':rows,
                    'mark_order':['explicit_session','implicit_session','group']}
                cliops=[r for r in self.state['operations'] if r['operation'] in CLI_OPERATIONS]
                self.state['cli']={'operation_count':len(cliops),'acknowledged_count':sum(r['status']=='ACKNOWLEDGED' for r in cliops),
                    'credential_contexts_cleared':all(r.get('credential_cleanup')=='CLEARED' for r in cliops)}
                if ([r['operation'] for r in self.state['operations']]!=list(FULL_OPERATIONS)
                        or any(r['status']!='ACKNOWLEDGED' for r in self.state['operations'])
                        or self.state['assertions']!=[{'id':x,'status':'PASS'} for x in REQUIREMENTS]
                        or self.state['cli']!={'operation_count':7,'acknowledged_count':7,'credential_contexts_cleared':True}
                        or len(rows)!=3 or any(r['attempt']!=1 or r['origin']!='created' or r['state']!='deleted' for r in rows)):
                    raise LocalChannelCliError('final Channel CLI evidence is incomplete')
                self.state.update(status='LOCAL_CHANNEL_CLI_PASS',
                    disposition='ONE_GROUP_TWO_SESSIONS_DELETED_HISTORY_RETAINED',
                    resource_disposition='LEDGER_3_TOMBSTONES_NO_CHANNEL_BINDING')
                self._save();return _copy(self.state)
        except BaseException as error:
            if self.state is not None:
                self.state['status']='CANCELLED' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'BLOCKED'
                self.state['error_type']=type(error).__name__
                if self.ledger is not None:
                    try:self.state['resources']['ledger_summary']={'resources':[dict(r) for r in self.ledger.resource_snapshot()]}
                    except BaseException:pass
                try:self._save()
                except BaseException:pass
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
            if isinstance(error,LocalChannelCliError):raise
            raise LocalChannelCliError('Channel CLI failed; inspect retained bounded evidence') from None
