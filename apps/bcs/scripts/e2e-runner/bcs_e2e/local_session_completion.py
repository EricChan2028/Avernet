"""Owned-local execution of the final three original Session helpers."""
from __future__ import annotations
import json
import os
from pathlib import Path
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request

from .errors import has_cancellation
from .inventory import current_manifest, executor_digest
from .ledger import Ledger, LedgerError
from .local_cli import require_cli_binary
from .local_role_identity import OwnedLocalRoleIdentity
from .local_session_contract import (
    COMPLETION_ASSERTION_IDS, PUBLIC_ASSERTION_IDS, REQUIREMENTS,
    SHARING_ASSERTION_IDS, session_source_binding)
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

CASE_ID='LOCAL-SESSION-FLOW-01'; CONTRACT='owned-local-session-flow-v1'; SUITE=CONTRACT
ROLES=('CEO','PM','ENG','QA'); SELECTED=REQUIREMENTS[6:]
JOURNAL_NAME='local-session-completion.json'; LEDGER_NAME='session-completion-resources.sqlite'
BINDING_NAME='session-completion-binding.json'; PLAN_NAME='session-completion-plan.json'
MAX_RESPONSE_BYTES=65_536
OPERATIONS=(
'lifecycle-visibility-before','lifecycle-visibility-public','lifecycle-visibility-public-read',
'lifecycle-group-create','lifecycle-session-create','lifecycle-session-patch','lifecycle-member-add',
'lifecycle-member-mute','lifecycle-chat','lifecycle-messages','lifecycle-member-remove',
'lifecycle-session-delete','lifecycle-session-list-absence','lifecycle-group-delete','lifecycle-group-deleted-404',
'lifecycle-visibility-restore','lifecycle-visibility-restored-read',
'complete-visibility-before','complete-visibility-public','complete-visibility-public-read',
'complete-group-create','complete-session-create','complete-delivery-get','complete-delivery-batch',
'complete-cancel-one','complete-cancel-message','complete-manual-resolve','complete-chat-complete',
'complete-chat-read','complete-settings','complete-service-create','complete-service-read',
'complete-service-foreign','complete-service-wrong-complete','complete-chat-delete','complete-service-delete',
'complete-group-delete','complete-group-deleted-404','complete-visibility-restore','complete-visibility-restored-read',
'collection-visibility-before','collection-visibility-public','collection-visibility-public-read',
'collection-group-create','collection-session-create','collection-initial-empty','collection-missing-participant',
'collection-collect','collection-collect-repeat','collection-pm-contains','collection-eng-empty',
'collection-qa-nonparticipant','collection-uncollect','collection-uncollect-repeat','collection-final-empty',
'collection-group-delete','collection-group-deleted-404','collection-visibility-restore','collection-visibility-restored-read')
CLI_OPERATIONS=('lifecycle-group-create','collection-group-create')
HELPERS=('lifecycle','complete','collection')

class LocalSessionCompletionError(RunStateError): pass

def _copy(value): return json.loads(json.dumps(value, allow_nan=False))

class OwnedLocalSessionCompletion:
    ROLES=ROLES
    def __init__(self, identities, binary:Path, binary_sha256:str):
        if (type(identities) is not OwnedLocalRoleIdentity or identities.fixture_set!='workspace'
                or tuple(identities.roles)!=ROLES or set(identities._verified)!=set(ROLES)):
            raise LocalSessionCompletionError('Session completion requires freshly verified CEO/PM/ENG/QA identities')
        identities._require_roles(ROLES); require_cli_binary(binary,binary_sha256)
        self.identities=identities; self.runtime=identities.runtime; self.root=self.runtime.root
        if self.runtime.store_messages is not True: raise LocalSessionCompletionError('store_messages=True required')
        parsed=urlsplit(identities.origin)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.path not in ('','/'):
            raise LocalSessionCompletionError('Session completion requires owned loopback origin')
        self.binary=binary.resolve(); self.binary_sha256=binary_sha256; self.executor_digest=executor_digest()
        self.source_binding=session_source_binding(); self.path=self.root/JOURNAL_NAME; self.ledger_path=self.root/LEDGER_NAME
        self.binding_path=self.root/BINDING_NAME; self.plan_path=self.root/PLAN_NAME
        self.public_binding_path=self.root/'session-binding.json'; self.public_path=self.root/'local-session-public.json'
        self.sharing_binding_path=self.root/'session-sharing-binding.json'; self.sharing_path=self.root/'local-session-sharing.json'
        self.binding=self._binding(); self.binding_digest=fingerprint(self.binding); self.plan=self._plan(); self.plan_digest=fingerprint(self.plan)
        self.public_journal=self._predecessor(self.public_path,self.public_binding_path,'LOCAL_SESSION_PUBLIC_PASS',PUBLIC_ASSERTION_IDS,3)
        self.sharing_journal=self._predecessor(self.sharing_path,self.sharing_binding_path,'LOCAL_SESSION_SHARING_PASS',SHARING_ASSERTION_IDS,13)
        if (fingerprint(self.public_journal)!=self.binding['public_journal_digest']
                or fingerprint(self.sharing_journal)!=self.binding['sharing_journal_digest']):
            raise LocalSessionCompletionError('Session predecessor digest differs from completion binding')
        self.fixture_digest=fingerprint(read(identities.fixtures.path)); self.state=None; self.ledger=None
        self.journal_digest=''; self.invalidated=False

    def _binding(self):
        keys={'schema_version','contract','suite','case_id','run_id','executor_digest','bcs_binary_sha256',
              'cli_binary_sha256','source_binding','selected_requirement_ids','parent_requirement_ids',
              'legacy_inventory','runtime_dir','store_messages','public_journal_digest','sharing_journal_digest'}
        try:
            v=read(self.binding_path)
            if (self.binding_path.is_symlink() or set(v)!=keys or v['schema_version']!=1 or v['contract']!=CONTRACT
                    or v['suite']!=SUITE or v['case_id']!=CASE_ID or v['run_id']!=self.runtime.run_id
                    or v['executor_digest']!=self.executor_digest or v['bcs_binary_sha256']!=self.runtime.binary_sha256
                    or v['cli_binary_sha256']!=self.binary_sha256 or v['source_binding']!=self.source_binding
                    or v['selected_requirement_ids']!=list(REQUIREMENTS) or v['parent_requirement_ids']!=list(REQUIREMENTS)
                    or v['legacy_inventory']!=current_manifest()['legacy_inventory']
                    or v['runtime_dir']!=str(self.root.resolve()) or v['store_messages'] is not True
                    or any(not isinstance(v[k],str) or len(v[k])!=64 for k in ('public_journal_digest','sharing_journal_digest'))): raise ValueError
            return v
        except Exception: raise LocalSessionCompletionError('Session completion binding is missing or changed') from None
    def _plan(self):
        try:
            v=read(self.plan_path); expected=dict(self.binding); expected.pop('public_journal_digest'); expected.pop('sharing_journal_digest')
            if self.plan_path.is_symlink() or v!=expected: raise ValueError
            return v
        except Exception: raise LocalSessionCompletionError('Frozen Session completion plan is missing or changed') from None
    def _predecessor(self,path,binding_path,status,assertion_ids,resources):
        try:
            value=read(path); binding=read(binding_path)
            expected_ids=assertion_ids
            if (path.is_symlink() or binding_path.is_symlink() or value.get('status')!=status
                    or value.get('run_id')!=self.runtime.run_id or value.get('executor_digest')!=self.executor_digest
                    or value.get('binding_digest')!=fingerprint(binding)
                    or value.get('source_binding_digest')!=fingerprint(self.source_binding)
                    or [row.get('id') for row in value.get('assertions',[])]!=list(expected_ids)
                    or any(row.get('status')!='PASS' for row in value.get('assertions',[]))
                    or value.get('resources',{}).get('ledger_resource_count',value.get('ledger',{}).get('resource_count'))!=resources
                    or any(r.get('state')!='deleted' for r in value.get('resources',{}).get('ledger',value.get('ledger',{}).get('resources',[])))): raise ValueError
            return value
        except Exception: raise LocalSessionCompletionError('Successful Session predecessor is missing or changed') from None
    @staticmethod
    def _exclusive(path):
        try: fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError: raise LocalSessionCompletionError('fresh completion journal and Ledger required') from None
        os.close(fd)
    def _save(self):
        if self.journal_digest and fingerprint(read(self.path))!=self.journal_digest:
            self.invalidated=True; raise LocalSessionCompletionError('Session completion journal changed')
        serialized=json.dumps(self.state,sort_keys=True,separators=(',',':'),allow_nan=False)
        secrets=[v for k,v in self.runtime.inputs.environment.items() if isinstance(v,str) and v and ('SECRET' in k or k.endswith('_TOKEN'))]
        secrets += [v[1] for v in self.identities._entries.values() if v[1]]
        if any(secret in serialized for secret in secrets):
            self.invalidated=True; raise LocalSessionCompletionError('credential leaked into completion evidence')
        write(self.path,self.state); self.journal_digest=fingerprint(self.state)
    def _active(self):
        if self.invalidated: raise LocalSessionCompletionError('Session completion authority invalidated')
        self.identities._require_roles(ROLES); require_cli_binary(self.binary,self.binary_sha256)
        if (executor_digest()!=self.executor_digest or fingerprint(read(self.identities.fixtures.path))!=self.fixture_digest
                or fingerprint(session_source_binding())!=fingerprint(self.source_binding)
                or fingerprint(self._binding())!=self.binding_digest or fingerprint(self._plan())!=self.plan_digest
                or fingerprint(read(self.public_path))!=self.binding['public_journal_digest']
                or fingerprint(read(self.sharing_path))!=self.binding['sharing_journal_digest']):
            raise LocalSessionCompletionError('Session completion source, fixture, plan, or predecessor changed')
    def _id(self,role): self._active(); return self.identities._entries[role][0]
    def _ledger_row(self,handle):
        try: return dict(self.ledger._get(handle))
        except (LedgerError,AttributeError): raise LocalSessionCompletionError('completion Ledger handle missing') from None
    def _owned(self,handle,kind,resource_id,parent=None,states=('live','deleted')):
        row=self._ledger_row(handle)
        if (row['kind']!=kind or row['case_id']!=CASE_ID or row['attempt']!=1
                or row['origin']!='created' or row['resource_id']!=resource_id
                or row['state'] not in states or row['parent']!=parent):
            raise LocalSessionCompletionError('resource is not the exact owned Ledger binding')
        return row
    def _group(self,h):
        value=self.state['resources']['groups'][h]
        self._owned(value['handle'],'group',value['id'],None)
        self._owned(value['initial_session_handle'],'session',value['initial_session_id'],value['handle'])
        return value
    def _session(self,h):
        value=self.state['resources']['sessions'][h];group=self._group(h)
        self._owned(value['handle'],'session',value['id'],group['handle'])
        return value
    def _service(self):
        value=self.state['resources']['service_session'];group=self._group('complete')
        self._owned(value['handle'],'session',value['id'],group['handle'])
        return value

    def _spec(self,op):
        pm,eng,qa=self._id('PM'),self._id('ENG'),self._id('QA')
        helper=op.split('-',1)[0]
        if op.endswith('visibility-before') or op.endswith('visibility-public-read') or op.endswith('visibility-restored-read'):
            return ('GET','/bots/'+quote(eng,safe='')+'/visibility',None,'ENG',200)
        if op.endswith('visibility-public'): return ('PUT','/bots/'+quote(eng,safe='')+'/visibility',{'visibility':'public'},'ENG',200)
        if op.endswith('visibility-restore'): return ('PUT','/bots/'+quote(eng,safe='')+'/visibility',{'visibility':'protected'},'ENG',200)
        if op in CLI_OPERATIONS:
            topic='e2e-session-completion-'+helper
            return ('POST','/groups',{'id':None,'label':None,'driver_bot':pm,'participants':[{'bot_uuid':pm,'role':None},{'bot_uuid':eng,'role':None}],'topic':topic,'create_initial_session':True},'PM',200)
        if op=='complete-group-create':
            return ('POST','/groups',{'driver_bot':pm,'label':'Release service','participants':[{'bot_uuid':pm,'role':'driver'},{'bot_uuid':eng,'role':'worker'}]},'PM',200)
        group=self._group(helper); gid=quote(group['id'],safe='')
        if op.endswith('session-create'):
            title={'lifecycle':'lifecycle-sess','complete':'Release sign-off','collection':'collect-sess'}[helper]
            return ('POST',f'/groups/{gid}/sessions',{'session_title':title},'PM',201)
        s=self._session(helper); sid=quote(s['id'],safe='')
        table={
        'lifecycle-session-patch':('PATCH',f'/sessions/{sid}',{'session_title':'renamed-sess'},'PM',200),
        'lifecycle-member-add':('POST',f'/sessions/{sid}/members',{'bot_uuid':eng},'PM',200),
        'lifecycle-member-mute':('PATCH',f'/sessions/{sid}/members/{quote(eng,safe="")}',{'mode':'muted'},'PM',200),
        'lifecycle-chat':('POST',f'/sessions/{sid}/chat',{'message':'hello-session'},'PM',200),
        'lifecycle-messages':('GET',f'/sessions/{sid}/messages?limit=50',None,None,200),
        'lifecycle-member-remove':('DELETE',f'/sessions/{sid}/members/{quote(eng,safe="")}',None,'PM',200),
        'lifecycle-session-delete':('DELETE',f'/sessions/{sid}?'+urlencode({'bot_id':pm}),None,None,200),
        'lifecycle-session-list-absence':('GET',f'/groups/{gid}/sessions',None,None,200),
        'complete-delivery-get':('GET',f'/openapi/v1/collaboration/messages/not-a-message/deliveries?session_id={quote(s["id"],safe="")}',None,'PM',200),
        'complete-delivery-batch':('POST',f'/openapi/v1/collaboration/sessions/{sid}/message-deliveries/query',{'message_ids':['not-a-message']},'PM',200),
        'complete-cancel-one':('POST',f'/openapi/v1/collaboration/messages/not-a-message/deliveries/not-a-delivery/cancel',{'session_id':s['id']},'PM',400),
        'complete-cancel-message':('POST','/openapi/v1/collaboration/messages/not-a-message/deliveries/cancel',{'session_id':s['id']},'PM',400),
        'complete-manual-resolve':('POST','/openapi/v1/collaboration/messages/not-a-message/deliveries/not-a-delivery/resolve',{'session_id':s['id'],'expected_state_version':3,'resolution':'confirmed_not_sent','reason':'Bot must not manually release uncertain work'},'PM',403),
        'complete-chat-complete':('POST',f'/sessions/{sid}/complete',{'output':{'summary':'release approved'}},'PM',200),
        'complete-chat-read':('GET',f'/sessions/{sid}',None,None,200),
        'complete-settings':('PATCH',f'/groups/{gid}/settings',{'service_spec':{'max_concurrency':2}},None,(200,410)),
        'collection-initial-empty':('GET',f'/groups/{gid}/sessions?'+urlencode({'participant':pm,'collected':'true'}),None,None,200),
        'collection-missing-participant':('GET',f'/groups/{gid}/sessions?collected=true',None,None,400),
        'collection-collect':('POST',f'/sessions/{sid}/collect',{},'PM',200),
        'collection-collect-repeat':('POST',f'/sessions/{sid}/collect',{},'PM',200),
        'collection-pm-contains':('GET',f'/groups/{gid}/sessions?'+urlencode({'participant':pm,'collected':'true'}),None,None,200),
        'collection-eng-empty':('GET',f'/groups/{gid}/sessions?'+urlencode({'participant':eng,'collected':'true'}),None,None,200),
        'collection-qa-nonparticipant':('POST',f'/sessions/{sid}/collect',{},'QA',404),
        'collection-uncollect':('DELETE',f'/sessions/{sid}/collect',None,'PM',200),
        'collection-uncollect-repeat':('DELETE',f'/sessions/{sid}/collect',None,'PM',200),
        'collection-final-empty':('GET',f'/groups/{gid}/sessions?'+urlencode({'participant':pm,'collected':'true'}),None,None,200),
        }
        if op=='complete-service-create': return ('POST',f'/services/{gid}/sessions',{'caller_id':'release-pipeline-1','session_title':'Automated release audit','input':{'commit':'abc123'},'meta':{'source':'e2e'}},'PM',202)
        if op.startswith('complete-service-') and op not in ('complete-service-create','complete-service-delete'):
            service=self._service(); ssid=quote(service['id'],safe='')
            if op=='complete-service-read': return ('GET',f'/services/{gid}/sessions/{ssid}',None,'PM',200)
            if op=='complete-service-foreign': return ('GET',f'/services/{gid}/sessions/{ssid}',None,'CEO',403)
            if op=='complete-service-wrong-complete': return ('POST',f'/sessions/{ssid}/complete',{'output':{'summary':'wrong endpoint'}},'PM',403)
        if op=='complete-chat-delete': return ('DELETE',f'/sessions/{sid}?'+urlencode({'bot_id':pm}),None,None,200)
        if op=='complete-service-delete':
            ssid=quote(self._service()['id'],safe='')
            return ('DELETE',f'/sessions/{ssid}?'+urlencode({'bot_id':'bot:'+pm}),None,None,200)
        if op.endswith('group-delete'): return ('DELETE',f'/groups/{gid}?'+urlencode({'bot_id':pm}),None,None,200)
        if op.endswith('group-deleted-404'): return ('GET',f'/groups/{gid}',None,None,404)
        if op not in table: raise LocalSessionCompletionError('operation lacks finite request contract')
        return table[op]

    def _admit(self,op,method,path,body,auth,status,handles=(),transport='LEGACY_HTTP',arguments=None):
        self._active(); index=len(self.state['operations'])
        if index>=len(OPERATIONS) or OPERATIONS[index]!=op: raise LocalSessionCompletionError('operation outside finite ordered plan')
        if (method,path,body,auth,status)!=self._spec(op): raise LocalSessionCompletionError('method, path, body, auth, or status differs')
        if transport=='PRODUCT_CLI':
            pm,eng=self._id('PM'),self._id('ENG'); helper=op.split('-',1)[0]
            expected=['create-group','--driver',pm,'--participants',pm+','+eng,'--topic','e2e-session-completion-'+helper]
            if op not in CLI_OPERATIONS or arguments!=expected or len(handles)!=2: raise LocalSessionCompletionError('CLI contract differs')
        elif transport!='LEGACY_HTTP' or arguments is not None: raise LocalSessionCompletionError('transport differs')
        if op.endswith('group-create'):
            if len(handles)!=2: raise LocalSessionCompletionError('Group create requires Group and implicit Session intents')
            group_row,session_row=(self._ledger_row(h) for h in handles)
            if (group_row['kind']!='group' or group_row['parent'] is not None
                    or session_row['kind']!='session' or session_row['parent']!=handles[0]):
                raise LocalSessionCompletionError('Group create intent pair kind or parent differs')
        elif op.endswith('session-create') or op=='complete-service-create':
            if len(handles)!=1: raise LocalSessionCompletionError('Session create requires one child intent')
            row=self._ledger_row(handles[0]);group=self._group(op.split('-',1)[0])
            if row['kind']!='session' or row['parent']!=group['handle']:
                raise LocalSessionCompletionError('Session create intent parent differs')
        for handle in handles:
            row=self._ledger_row(handle)
            if (row['case_id']!=CASE_ID or row['attempt']!=1 or row['state']!='intended'
                    or row['origin']!='unknown' or row['resource_id'] is not None):
                raise LocalSessionCompletionError('resource handle is not a fresh owned intent')
        if op in ('lifecycle-session-delete','complete-chat-delete','complete-service-delete'):
            target=self._session('lifecycle') if op.startswith('lifecycle') else self._session('complete') if op=='complete-chat-delete' else self._service()
            self._owned(target['handle'],'session',target['id'],self._group(op.split('-',1)[0])['handle'],('live',))
            if op.split('-',1)[0] not in self.state['cleanup_admitted']:
                raise LocalSessionCompletionError('Session delete requires prior local deletion admission')
        if op.endswith('group-delete'):
            helper=op.split('-',1)[0];group=self._group(helper)
            self._owned(group['handle'],'group',group['id'],None,('live',))
            if helper not in self.state['cleanup_admitted']:
                raise LocalSessionCompletionError('Group delete requires prior local deletion admission')
    def _append(self,op,method,path,mutation,handles,request_id,transport='LEGACY_HTTP'):
        identifier(request_id,'completion request ID')
        event={'operation':op,'transport':transport,'method':method,'path':path,'submitted_request_id':request_id,
               'kind':'MUTATION' if mutation else 'CHECK','status':'INTENDED','transitions':[{'status':'INTENDED'}]}
        if handles: event.update(resource_handles=list(handles),resource_binding='UNKNOWN')
        self.state['operations'].append(event); self._save(); return event
    def _failed(self,event,error,handles,mutation):
        self.invalidated=True; event['status']='UNKNOWN' if mutation else 'FAILED'; event['error_type']=type(error).__name__
        event['transitions'].append({'status':event['status'],'error_type':type(error).__name__})
        for h in handles:
            try:self.ledger.unknown(h)
            except BaseException:pass
        try:self._save()
        except BaseException:pass
        if has_cancellation(error): raise KeyboardInterrupt() from None
    def _http(self,op,handles=()):
        method,path,body,auth,status=self._spec(op); mutation=method in ('POST','PUT','PATCH','DELETE')
        self._admit(op,method,path,body,auth,status,handles)
        request_id='e2e-session-completion-'+uuid.uuid4().hex
        event=self._append(op,method,path,mutation,handles,request_id)
        headers={'Accept':'application/json','X-Request-ID':request_id}; raw=None
        if auth: headers['Authorization']='Bearer '+self.identities._entries[auth][1]
        if body is not None: headers['Content-Type']='application/json'; raw=json.dumps(body,separators=(',',':'),allow_nan=False).encode()
        try:
            request=Request(self.identities.origin+path,method=method,data=raw,headers=headers)
            expected_statuses=status if isinstance(status,tuple) else (status,)
            try: response=self.identities.opener.open(request,timeout=5)
            except HTTPError as error:
                if error.code not in expected_statuses: raise
                response=error
            with response: actual=response.status; payload=response.read(MAX_RESPONSE_BYTES+1)
            self._active()
            if actual not in expected_statuses or len(payload)>MAX_RESPONSE_BYTES: raise LocalSessionCompletionError('HTTP status or byte budget differs')
            value=json.loads(payload.decode(),object_pairs_hook=unique_keys)
            if not isinstance(value,(dict,list)): raise LocalSessionCompletionError('HTTP response JSON shape unsupported')
            event.update(status='ACKNOWLEDGED',http_status=actual); event['transitions'].append({'status':'ACKNOWLEDGED','http_status':actual})
            if handles:event['resource_binding']='PENDING_RESPONSE_VALIDATION'
            self._save(); return value
        except BaseException as error:
            if isinstance(error,HTTPError): error.close()
            self._failed(event,error,handles,mutation)
            raise LocalSessionCompletionError('HTTP outcome unconfirmed; no retry, adoption, or cleanup') from None

    def _visibility(self,op,expected):
        value=self._http(op); data=value.get('data')
        if value.get('success') is not True or data.get('bot_uuid')!=self._id('ENG') or data.get('visibility')!=expected:
            raise LocalSessionCompletionError('ENG visibility response differs')
        return expected
    def _intent_group(self,helper,cli=None):
        request_id='e2e-completion-'+helper+'-group-'+uuid.uuid4().hex
        handles=self.ledger.group_creation_intent(CASE_ID,1,request_id)
        reply=cli.create_group(helper,handles,request_id) if cli else self._http(helper+'-group-create',handles)
        try:
            gid=identifier(reply.get('id'),'completion Group ID'); initial=identifier(reply.get('session_id'),'implicit Session ID')
            self.ledger.observed(handles[0],gid,origin='created',actual_request_id=request_id); self.ledger.observed(handles[1],initial,origin='created')
            self.state['resources']['groups'][helper]={'id':gid,'handle':handles[0],'initial_session_id':initial,'initial_session_handle':handles[1],'attempt':1}
            event=self.state['operations'][-1]; event['candidate_ids']={'group':gid,'initial_session':initial};event['resource_binding']='BOUND';self._save()
        except BaseException:
            for h in handles:
                try:self.ledger.unknown(h)
                except BaseException:pass
            self.invalidated=True;raise LocalSessionCompletionError('Group response could not be bound') from None
        return self._group(helper)
    def _intent_session(self,helper):
        group=self._group(helper); request_id='e2e-completion-'+helper+'-session-'+uuid.uuid4().hex
        handle=self.ledger.intent(CASE_ID,1,'session',request_id,group['handle']); reply=self._http(helper+'-session-create',(handle,))
        try:
            sid=identifier(reply.get('session_id'),'explicit Session ID')
            if reply.get('id')!=sid or reply.get('group_id')!=group['id'] or sid==group['initial_session_id']:raise ValueError
            self.ledger.observed(handle,sid,origin='created',actual_request_id=request_id)
            self.state['resources']['sessions'][helper]={'id':sid,'handle':handle,'group_id':group['id'],'attempt':1}
            event=self.state['operations'][-1];event['candidate_ids']={'session':sid};event['resource_binding']='BOUND';self._save()
        except BaseException:
            try:self.ledger.unknown(handle)
            except BaseException:pass
            self.invalidated=True;raise LocalSessionCompletionError('Session response could not be bound') from None
        return self._session(helper)
    def _service_intent(self):
        group=self._group('complete'); request_id='e2e-completion-service-'+uuid.uuid4().hex
        handle=self.ledger.intent(CASE_ID,1,'session',request_id,group['handle']); reply=self._http('complete-service-create',(handle,))
        try:
            sid=identifier(reply.get('session_id'),'service Session ID')
            if reply.get('session_kind')!='service_invocation' or reply.get('group_id')!=group['id']:raise ValueError
            self.ledger.observed(handle,sid,origin='created',actual_request_id=request_id)
            self.state['resources']['service_session']={'id':sid,'handle':handle,'group_id':group['id'],'attempt':1,'session_kind':reply.get('session_kind')}
            event=self.state['operations'][-1];event['candidate_ids']={'session':sid};event['resource_binding']='BOUND';self._save();return reply
        except BaseException:
            try:self.ledger.unknown(handle)
            except BaseException:pass
            self.invalidated=True;raise LocalSessionCompletionError('Service Session response could not be bound') from None
    @staticmethod
    def _items(value):
        items=value.get('items') if isinstance(value,dict) else None
        if not isinstance(items,list): raise LocalSessionCompletionError('Session list response lacks items')
        return items
    def _record_admission(self,admission):
        helper=admission.get('helper')
        if helper not in HELPERS or helper in self.state['cleanup_admitted']:
            raise LocalSessionCompletionError('local deletion admission is invalid or duplicated')
        self.state['cleanup_admitted'][helper]=admission
        self.state['deletion_admissions'].append(admission);self._save()
    def _record_check(self,helper,evidence):
        self.state['checks'].append({'id':helper,'status':'PASS','evidence':evidence});self._save()
    @staticmethod
    def _settings_form(http_status,value):
        # Dev PR #3173 retired Group service_spec policies station-wide. The
        # current station answers this legacy patch with exact HTTP 410, while
        # an acknowledged 200 remains accepted for compatible builds.
        if http_status==410:
            if value!={'error':'service_policy_retired','message':'service_spec settings have been retired'}:
                raise LocalSessionCompletionError('retired service settings response differs')
            return 'retired_410'
        if (http_status==200 and isinstance(value,dict)
                and value.get('service_spec',{}).get('max_concurrency')==2):
            return 'service_spec_ack'
        raise LocalSessionCompletionError('service concurrency acknowledgement differs')
    def _assert(self, assertion_id, evidence):
        rows=[r for r in self.state['assertions'] if r['id']==assertion_id]
        if (assertion_id not in COMPLETION_ASSERTION_IDS or len(rows)!=1
                or rows[0]['status']!='NOT_RUN'):
            raise LocalSessionCompletionError('completion assertion ID is invalid or duplicated')
        rows[0].update(status='PASS',evidence=evidence);self._save()
    def _cleanup(self,helper,session_handles,admission=None,history=None):
        from .local_session_completion_storage import deletion_admission,deletion_proof,group_history
        if admission is None: admission=deletion_admission(self,helper)
        if history is None: history=group_history(self,helper)
        if helper not in self.state['cleanup_admitted']: self._record_admission(admission)
        deleted=self._http(helper+'-group-delete')
        if deleted!={'deleted':True,'id':self._group(helper)['id']}:raise LocalSessionCompletionError('Group delete acknowledgement differs')
        missing=self._http(helper+'-group-deleted-404')
        if missing.get('status')!=404 or missing.get('code')!='group_not_found' or missing.get('params')!={'group_id':self._group(helper)['id']}:
            raise LocalSessionCompletionError('Group deleted 404 differs')
        proof=deletion_proof(self,helper,history)
        for h in session_handles:self.ledger.mark_deleted(h)
        self.ledger.mark_deleted(self._group(helper)['handle'])
        self.state['deletion_proofs'].append({'helper':helper,'sqlite':proof,'ledger_mark_order':['session']*len(session_handles)+['group']})
        self.state['helper_evidence'].setdefault(helper,{})['cleanup']={'cleanup':'PASS','sqlite':proof}
        self._record_check(helper,{'cleanup':'PASS','sqlite':proof})

    def run(self):
        from .local_session_completion_cli import CompletionCliDriver
        from .local_session_completion_storage import fixture_snapshot,predecessor_snapshot,unrelated_snapshot,verify_lifecycle_message
        self._active()
        if self.path.exists() or self.ledger_path.exists():raise LocalSessionCompletionError('fresh completion evidence paths required')
        self._exclusive(self.path)
        self.state={'schema_version':1,'contract':CONTRACT,'suite':SUITE,'case_id':CASE_ID,'run_id':self.runtime.run_id,'status':'RUNNING',
          'executor_digest':self.executor_digest,'binding_digest':self.binding_digest,'plan_digest':self.plan_digest,
          'source_binding_digest':fingerprint(self.source_binding),'predecessors':{'public':self.binding['public_journal_digest'],'sharing':self.binding['sharing_journal_digest']},
          'operations':[],'checks':[],'resources':{'groups':{},'sessions':{}},'assertions':[{'id':x,'status':'NOT_RUN'} for x in COMPLETION_ASSERTION_IDS],
          'deletion_admissions':[],'cleanup_admitted':{},'deletion_proofs':[],'visibility':{},'histories':{},'fixture_state':{'preserved':False},'unrelated_state':{'preserved':False},
          'execution_scope':{'runtime':'OWNED_LOCAL_FOREGROUND','engine':'NOT_STARTED','receiver':'NOT_OPENED','transport':'NOT_TESTED_WITH_CONNECTED_ENGINES',
                             'service_business_execution':'NOT_CLAIMED','manual_chat_terminality':'ONLY_COMPLETE_HELPER'},
          'orchestration_difference':{'cleanup_timing':'ORIGINAL_PER_HELPER_BEFORE_NEXT_CREATE','max_groups_as_driver_raised':False}}
        self._save()
        try:
            with Ledger(self.ledger_path,self.runtime.run_id,self.binding_digest,create=True) as ledger:
                self.ledger=ledger;fixture_before=fixture_snapshot(self);unrelated_before=unrelated_snapshot(self);pred_before=predecessor_snapshot(self)
                self.state['fixture_state']['before']=fixture_before;self.state['unrelated_state']['before']=unrelated_before;self.state['histories']['predecessor_before']=pred_before;self._save()
                cli=CompletionCliDriver(self,self.binary,self.binary_sha256);cli.require_pm_token()
                # lifecycle
                self.state['visibility']['lifecycle']={'before':self._visibility('lifecycle-visibility-before','protected')}
                self.state['visibility']['lifecycle']['admitted']=self._visibility('lifecycle-visibility-public','public');self.state['visibility']['lifecycle']['public_read']=self._visibility('lifecycle-visibility-public-read','public')
                g=self._intent_group('lifecycle',cli);s=self._intent_session('lifecycle')
                self._assert('SESSION.lifecycle.session-create',{'session_id':s['id'],'group_id':g['id']})
                patched=self._http('lifecycle-session-patch')
                if patched.get('session_title')!='renamed-sess':raise LocalSessionCompletionError('session rename differs')
                self._assert('SESSION.lifecycle.session-patch',{'session_title':patched['session_title']})
                added=self._http('lifecycle-member-add')
                if not any(p.get('bot_uuid')==self._id('ENG') for p in added.get('participants',[])):raise LocalSessionCompletionError('ENG add differs')
                self._assert('SESSION.lifecycle.member-add',{'participant_count':len(added.get('participants',[]))})
                muted=self._http('lifecycle-member-mute')
                if not any(p.get('bot_uuid')==self._id('ENG') and p.get('mode')=='muted' for p in muted.get('participants',[])):raise LocalSessionCompletionError('ENG mute differs')
                self._assert('SESSION.lifecycle.member-mute',{'mode':'muted'})
                chat=self._http('lifecycle-chat')
                if chat.get('session_id')!=s['id'] or chat.get('delivered_count')!=0 or chat.get('delivered') is not False:raise LocalSessionCompletionError('disconnected lifecycle delivery response differs')
                self._assert('SESSION.lifecycle.chat',{'session_id':chat['session_id'],'delivered_count':chat['delivered_count']})
                messages=self._http('lifecycle-messages');message_proof=verify_lifecycle_message(self,messages)
                self._assert('SESSION.lifecycle.messages',{'message_proof':message_proof})
                removed=self._http('lifecycle-member-remove')
                if any(p.get('bot_uuid')==self._id('ENG') for p in removed.get('participants',[])):raise LocalSessionCompletionError('ENG removal differs')
                self._assert('SESSION.lifecycle.member-remove',{'eng_absent':True})
                from .local_session_completion_storage import deletion_admission,group_history
                admission=deletion_admission(self,'lifecycle');history=group_history(self,'lifecycle');self._record_admission(admission)
                lifecycle_deleted=self._http('lifecycle-session-delete')
                if lifecycle_deleted!={'deleted':True,'session_id':s['id']}:raise LocalSessionCompletionError('creator session delete differs')
                self._assert('SESSION.lifecycle.session-delete',{'deleted':True,'session_id':s['id']})
                self.ledger.mark_deleted(s['handle'])
                lifecycle_absence=self._items(self._http('lifecycle-session-list-absence'))
                if any((x.get('session_id') or x.get('id'))==s['id'] for x in lifecycle_absence):raise LocalSessionCompletionError('deleted Session remains listed')
                self._assert('SESSION.lifecycle.session-delete-verified',{'listed':False})
                self.state['helper_evidence']={'lifecycle':{'chat_http_status':200,'delivery_count':0,'transport':'NOT_TESTED','message':message_proof}}
                self._cleanup('lifecycle',(g['initial_session_handle'],),admission,history)
                self.state['visibility']['lifecycle']['after']=self._visibility('lifecycle-visibility-restore','protected');self.state['visibility']['lifecycle']['restored']=self._visibility('lifecycle-visibility-restored-read','protected')=='protected'
                # complete and invoke
                self.state['visibility']['complete']={'before':self._visibility('complete-visibility-before','protected')};self.state['visibility']['complete']['admitted']=self._visibility('complete-visibility-public','public');self.state['visibility']['complete']['public_read']=self._visibility('complete-visibility-public-read','public')
                g=self._intent_group('complete');s=self._intent_session('complete')
                self._assert('SESSION.complete-invoke.service-group-create',{'group_id':g['id']})
                self._assert('SESSION.complete-invoke.service-group-id',{'group_id':g['id']})
                self._assert('SESSION.complete-invoke.signoff-session-create',{'session_id':s['id']})
                delivery_one=self._http('complete-delivery-get');delivery_batch=self._http('complete-delivery-batch')
                if delivery_one!=[] or delivery_batch!=[]:raise LocalSessionCompletionError('delivery query fabricated state')
                self._assert('SESSION.complete-invoke.unmanaged-message-inspect',{'http_status':200})
                self._assert('SESSION.complete-invoke.unmanaged-message-no-deliveries',{'delivery_rows':len(delivery_one)})
                self._assert('SESSION.complete-invoke.delivery-batch-query',{'http_status':200})
                self._assert('SESSION.complete-invoke.delivery-batch-no-rows',{'delivery_rows':len(delivery_batch)})
                cancel_one=self._http('complete-cancel-one')
                if cancel_one.get('error')!='Bad Request':raise LocalSessionCompletionError('missing-message cancellation response differs')
                self._assert('SESSION.complete-invoke.cancel-missing-delivery',{'error':cancel_one['error']})
                cancel_message=self._http('complete-cancel-message')
                if cancel_message.get('error')!='Bad Request':raise LocalSessionCompletionError('missing-message cancellation response differs')
                self._assert('SESSION.complete-invoke.cancel-missing-message',{'error':cancel_message['error']})
                manual=self._http('complete-manual-resolve')
                if manual.get('error')!='Forbidden':raise LocalSessionCompletionError('Bot manual resolution guard differs')
                self._assert('SESSION.complete-invoke.manual-resolve-forbidden',{'error':manual['error']})
                complete=self._http('complete-chat-complete')
                if complete.get('status')!='completed' or complete.get('output',{}).get('summary')!='release approved':raise LocalSessionCompletionError('chat completion differs')
                self._assert('SESSION.complete-invoke.chat-complete',{'http_status':200})
                self._assert('SESSION.complete-invoke.chat-status-completed',{'status':complete['status']})
                self._assert('SESSION.complete-invoke.chat-output-stored',{'output':complete['output']})
                readback=self._http('complete-chat-read')
                if readback.get('status')!='completed' or readback.get('output',{}).get('summary')!='release approved':raise LocalSessionCompletionError('completion readback differs')
                self._assert('SESSION.complete-invoke.chat-readback',{'http_status':200})
                self._assert('SESSION.complete-invoke.chat-readback-status',{'status':readback['status']})
                self._assert('SESSION.complete-invoke.chat-readback-output',{'output':readback['output']})
                settings=self._http('complete-settings')
                settings_form=self._settings_form(self.state['operations'][-1]['http_status'],settings)
                service=self._service_intent();ssid=service['session_id']
                self._assert('SESSION.complete-invoke.service-start',{'session_id':ssid,'settings_form':settings_form})
                self._assert('SESSION.complete-invoke.service-session-id',{'session_id':ssid})
                self._assert('SESSION.complete-invoke.service-kind',{'session_kind':self.state['resources']['service_session'].get('session_kind')})
                own=self._http('complete-service-read')
                if own.get('group_id')!=g['id'] or own.get('session_title')!='Automated release audit':raise LocalSessionCompletionError('service caller read differs')
                self._assert('SESSION.complete-invoke.service-caller-read',{'http_status':200})
                self._assert('SESSION.complete-invoke.service-belongs-group',{'group_id':own['group_id']})
                self._assert('SESSION.complete-invoke.service-title',{'session_title':own['session_title']})
                foreign=self._http('complete-service-foreign')
                if foreign.get('error')!='forbidden':raise LocalSessionCompletionError('foreign service caller guard differs')
                self._assert('SESSION.complete-invoke.service-foreign-forbidden',{'error':foreign['error']})
                self._assert('SESSION.complete-invoke.service-isolation',{'caller_scope':'OTHER_BOT'})
                wrong=self._http('complete-service-wrong-complete')
                if wrong.get('error')!='forbidden' or 'service sessions cannot be completed via this endpoint' not in wrong.get('message',''):raise LocalSessionCompletionError('wrong completion endpoint guard differs')
                self._assert('SESSION.complete-invoke.service-wrong-complete',{'error':wrong['error']})
                self._assert('SESSION.complete-invoke.service-completion-guard',{'guard':'CHAT_COMPLETION_ENDPOINT'})
                self._assert('SESSION.complete-invoke.service-guard-endpoint',{'message':wrong['message']})
                admission=deletion_admission(self,'complete');history=group_history(self,'complete');self._record_admission(admission)
                chat_deleted=self._http('complete-chat-delete')
                if chat_deleted!={'deleted':True,'session_id':s['id']}:raise LocalSessionCompletionError('completed chat cleanup differs')
                self._assert('SESSION.complete-invoke.chat-cleanup',{'deleted':True,'session_id':s['id']})
                self.ledger.mark_deleted(s['handle'])
                service_deleted=self._http('complete-service-delete')
                if service_deleted!={'deleted':True,'session_id':ssid}:raise LocalSessionCompletionError('service cleanup differs')
                self._assert('SESSION.complete-invoke.service-cleanup',{'deleted':True,'session_id':ssid})
                self.ledger.mark_deleted(self.state['resources']['service_session']['handle'])
                self.state['helper_evidence']['complete']={'manual_chat_terminality':'COMPLETED','service_business_execution':'NOT_CLAIMED','empty_delivery_rows':True}
                self._cleanup('complete',(g['initial_session_handle'],),admission,history)
                self._assert('SESSION.complete-invoke.service-group-cleanup',{'group_id':g['id'],'http_status':404})
                self.state['visibility']['complete']['after']=self._visibility('complete-visibility-restore','protected');self.state['visibility']['complete']['restored']=self._visibility('complete-visibility-restored-read','protected')=='protected'
                # collection
                self.state['visibility']['collection']={'before':self._visibility('collection-visibility-before','protected')};self.state['visibility']['collection']['admitted']=self._visibility('collection-visibility-public','public');self.state['visibility']['collection']['public_read']=self._visibility('collection-visibility-public-read','public')
                g=self._intent_group('collection',cli);s=self._intent_session('collection')
                self._assert('SESSION.collection.session-create',{'session_id':s['id'],'group_id':g['id']})
                initial=self._items(self._http('collection-initial-empty'))
                if initial!=[]:raise LocalSessionCompletionError('initial collected list not empty')
                self._assert('SESSION.collection.initial-empty',{'items':len(initial)})
                missing=self._http('collection-missing-participant')
                if missing.get('error')!='invalid_params' or 'participant' not in missing.get('message',''):raise LocalSessionCompletionError('missing participant guard differs')
                self._assert('SESSION.collection.missing-participant-rejected',{'error':missing['error']})
                collect_one=self._http('collection-collect')
                if collect_one!={'collected':True,'session_id':s['id']}:raise LocalSessionCompletionError('collect idempotency differs')
                self._assert('SESSION.collection.collect',{'collected':True,'session_id':collect_one['session_id']})
                collect_repeat=self._http('collection-collect-repeat')
                if collect_repeat!={'collected':True,'session_id':s['id']}:raise LocalSessionCompletionError('collect idempotency differs')
                self._assert('SESSION.collection.collect-idempotent',{'http_status':200,'collected':True})
                pmitems=self._items(self._http('collection-pm-contains'))
                if not any((x.get('session_id') or x.get('id'))==s['id'] and x.get('collected') is True for x in pmitems):raise LocalSessionCompletionError('PM collected list missing Session')
                self._assert('SESSION.collection.pm-contains',{'session_id':s['id'],'collected':True})
                engitems=self._items(self._http('collection-eng-empty'))
                if any((x.get('session_id') or x.get('id'))==s['id'] for x in engitems):raise LocalSessionCompletionError('ENG collection isolation differs')
                self._assert('SESSION.collection.eng-isolation',{'items':len(engitems)})
                qa=self._http('collection-qa-nonparticipant')
                if qa != {'error':'participant '+self._id('QA')+' not in session '+s['id']}:raise LocalSessionCompletionError('QA nonparticipant response differs')
                self._assert('SESSION.collection.qa-nonparticipant',{'http_status':404,'error':qa['error']})
                uncollect_one=self._http('collection-uncollect')
                if uncollect_one!={'collected':False,'session_id':s['id']}:raise LocalSessionCompletionError('uncollect idempotency differs')
                self._assert('SESSION.collection.uncollect',{'collected':False,'session_id':uncollect_one['session_id']})
                uncollect_repeat=self._http('collection-uncollect-repeat')
                if uncollect_repeat!={'collected':False,'session_id':s['id']}:raise LocalSessionCompletionError('uncollect idempotency differs')
                self._assert('SESSION.collection.uncollect-idempotent',{'http_status':200,'collected':False})
                final=self._items(self._http('collection-final-empty'))
                if final!=[]:raise LocalSessionCompletionError('final collected list not empty')
                self._assert('SESSION.collection.final-empty',{'items':len(final)})
                self.state['helper_evidence']['collection']={'initial_empty':True,'idempotent_collect':True,'per_bot_isolation':True,'qa_nonparticipant_404':True,'final_empty':True}
                self._cleanup('collection',(g['initial_session_handle'],s['handle']))
                self.state['visibility']['collection']['after']=self._visibility('collection-visibility-restore','protected');self.state['visibility']['collection']['restored']=self._visibility('collection-visibility-restored-read','protected')=='protected'
                fixture_after=fixture_snapshot(self);unrelated_after=unrelated_snapshot(self,tuple(self._group(h)['id'] for h in HELPERS));pred_after=predecessor_snapshot(self)
                if fixture_after!=fixture_before or unrelated_after!=unrelated_before or pred_after!=pred_before:raise LocalSessionCompletionError('fixture, unrelated, or predecessor state changed')
                self.state['fixture_state'].update(after=fixture_after,preserved=True);self.state['unrelated_state'].update(after=unrelated_after,preserved=True);self.state['histories']['predecessor_after']=pred_after
                rows=[{k:r[k] for k in ('handle','case_id','attempt','kind','parent','request_id','origin','resource_id','state')} for r in self.ledger.resource_snapshot()]
                cliops=[r for r in self.state['operations'] if r['operation'] in CLI_OPERATIONS]
                self.state['cli']={'operation_count':len(cliops),'acknowledged_count':sum(r['status']=='ACKNOWLEDGED' for r in cliops),'credential_contexts_cleared':all(r.get('credential_cleanup')=='CLEARED' for r in cliops)}
                self.state['resources'].update(group_count=3,session_count=7,ledger_resource_count=len(rows),ledger_deleted_count=sum(r['state']=='deleted' for r in rows),ledger=rows)
                if ([r['operation'] for r in self.state['operations']]!=list(OPERATIONS) or any(r['status']!='ACKNOWLEDGED' for r in self.state['operations'])
                        or self.state['cli']!={'operation_count':2,'acknowledged_count':2,'credential_contexts_cleared':True}
                        or len(rows)!=10 or any(r['origin']!='created' or r['state']!='deleted' or r['attempt']!=1 for r in rows)
                        or [r['id'] for r in self.state['assertions']]!=list(COMPLETION_ASSERTION_IDS) or any(r['status']!='PASS' for r in self.state['assertions']) or len(self.state['deletion_proofs'])!=3):
                    raise LocalSessionCompletionError('final completion evidence incomplete')
                self.state.update(status='LOCAL_SESSION_COMPLETION_PASS',disposition='THREE_GROUPS_SEVEN_SESSIONS_DELETED_HISTORY_RETAINED',resource_disposition='LEDGER_10_TOMBSTONES')
                self._save();return _copy(self.state)
        except BaseException as error:
            cancelled=has_cancellation(error)
            if self.state is not None:
                self.state['status']='CANCELLED' if cancelled else 'BLOCKED';self.state['error_type']=type(error).__name__
                for assertion in self.state['assertions']:
                    if assertion['status']=='NOT_RUN':assertion['status']='CANCELLED' if cancelled else 'FAIL';break
                if self.ledger is not None:
                    try:self.state['resources']['ledger']=[dict(r) for r in self.ledger.resource_snapshot()]
                    except BaseException:pass
                try:self._save()
                except BaseException:pass
            if cancelled:raise KeyboardInterrupt() from None
            if isinstance(error,LocalSessionCompletionError):raise
            raise LocalSessionCompletionError('Session completion failed; inspect retained evidence') from None
