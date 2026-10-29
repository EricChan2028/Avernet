"""Complete original Structured Story, including its implicit initial execution."""
import copy
from http.client import HTTPConnection
import json
import time
import uuid
from urllib.parse import quote, urlsplit

from .inventory import executor_digest
from .local_direct_chat_storage import fixture_snapshot
from .local_network_reporting import _exclusive
from .local_role_identity import OwnedLocalRoleIdentity
from .local_structured_contract import CASE_ID, SUITE, REQUIREMENTS, structured_source_binding
from .local_template_probe import TEMPLATE
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

INSTRUCTION = 'Draft a concise release recommendation.'
REVISED = 'Draft and verify a concise release recommendation.'
EXTRAS = ('fixture-public','implicit-read','implicit-cancel','implicit-terminal','fixture-restore')
ORDER = ('fixture-public', *REQUIREMENTS[:3], 'implicit-read', *REQUIREMENTS[3:16],
         'implicit-cancel', 'implicit-terminal', REQUIREMENTS[16], 'fixture-restore')


def require(value, message='Structured Story finite contract differs'):
    if not value: raise RunStateError(message)


def definition_yaml(ceo):
    return f'''name: E2E Guided Answer
participants:
  driver:
    bot_id: "{ceo}"
    required: true
runtime:
  kind: state_machine
  state_machine:
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Draft answer
        assignee:
          type: bot_binding
          binding: driver
        instruction: {INSTRUCTION}
        final_output: true'''


class OwnedLocalStructured:
    ROLES = ('CEO','PM','ENG','QA')

    def __init__(self, identities):
        require(type(identities) is OwnedLocalRoleIdentity and identities.fixture_set == 'workspace'
                and identities.roles == self.ROLES and set(identities._verified) == set(self.ROLES),
                'Structured Story requires verified fresh owned workspace roles')
        identities._require_roles(self.ROLES)
        self.identities, self.runtime = identities, identities.runtime
        require(type(self.runtime) is OwnedBcsProcess and self.runtime.provider_sink is None
                and self.runtime.template_asset is not None)
        self.root, self.run_id = self.runtime.root, self.runtime.run_id
        self.source = executor_digest(); self.source_binding = structured_source_binding()
        self.path = self.root/'local-structured.json'
        self.binding = read(self.root/'structured-binding.json')
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.config_digest = file_sha256(self.runtime.inputs.config_path)
        require(self.binding['contract'] == SUITE and self.binding['case_id'] == CASE_ID
                and self.binding['run_id'] == self.run_id and self.binding['executor_digest'] == self.source
                and self.binding['source_binding'] == self.source_binding
                and self.binding['selected_requirement_ids'] == list(REQUIREMENTS))
        target = urlsplit(identities.origin)
        require(target.scheme == 'http' and target.hostname == '127.0.0.1' and target.port
                and target.netloc == '127.0.0.1:'+str(target.port) and not target.path
                and not target.query and not target.fragment)
        self.port = target.port
        self.ceo = identities._entries['CEO'][0]; self.pm = identities._entries['PM'][0]
        self.state = None; self._digest = ''; self._claims = set(); self._attempted = False
        self._active()
        require(not self.path.exists() and not self.path.is_symlink(), 'Structured journal exists')

    def _active(self):
        self.identities._require_roles(self.ROLES)
        require(self.runtime.root == self.root and self.runtime.run_id == self.run_id
                and executor_digest() == self.source
                and file_sha256(self.runtime.inputs.config_path) == self.config_digest
                and fingerprint(read(self.identities.fixtures.path)) == self.fixture_digest
                and read(self.root/'structured-binding.json') == self.binding,
                'Structured bindings changed during execution')
        from .local_template_asset import LocalTemplateAsset
        asset=self.runtime.template_asset
        LocalTemplateAsset(self.root/'templates',asset.manifest_path,asset.manifest_sha256).validate()
        require(asset.manifest_sha256==self.binding['template_manifest_sha256'])
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest, 'Structured journal changed')

    def _save(self):
        if self._digest: require(fingerprint(read(self.path)) == self._digest)
        text = json.dumps(self.state)
        require(not any(token in text for _,token in self.identities._entries.values()), 'credential in evidence')
        write(self.path,self.state); self._digest = fingerprint(self.state)

    def _spec(self, operation):
        state = self.state
        group = state.get('group_id',''); explicit = state.get('explicit',{}).get('run_id','')
        implicit = state.get('implicit',{}).get('run_id','')
        if operation in ('fixture-public','fixture-restore'):
            return 'PUT','/bots/'+quote(self.pm,safe='')+'/visibility',{'visibility':'public' if operation=='fixture-public' else 'protected'},200,'PM'
        if operation in ('implicit-read','implicit-terminal'):
            identifier(implicit,'implicit Run'); return 'GET','/state-machine-runs/'+quote(implicit,safe=''),None,200,'HUMAN'
        if operation == 'implicit-cancel':
            identifier(implicit,'implicit Run'); return 'POST','/state-machine-runs/'+quote(implicit,safe='')+'/cancel',{'reason':'E2E implicit fixture completed'},200,'HUMAN'
        require(operation in REQUIREMENTS)
        row = self.source_binding['requirements'][REQUIREMENTS.index(operation)]
        path = row['path'].replace('${group_id}',quote(group,safe='')).replace('${run_id}',quote(explicit,safe='')).replace('${BOT_CEO_UUID}',quote(self.ceo,safe=''))
        if '${group_id}' in row['path']: identifier(group,'owned Group')
        if '${run_id}' in row['path']: identifier(explicit,'owned explicit Run')
        body = None; code = 200
        index = REQUIREMENTS.index(operation)
        if index == 2:
            body = {'driver_bot':self.ceo,'label':'Structured release review',
                'participants':[{'bot_uuid':self.ceo},{'bot_uuid':self.pm}],
                'group_strategy':'state_machine','collaboration_definition_yaml':definition_yaml(self.ceo)}
        elif index == 5:
            body = {'base_definition':{'id':state['definition_id'],'version':state['base_version']},
                    'definition_yaml':definition_yaml(self.ceo).replace(INSTRUCTION,REVISED)}
        elif index == 6:
            body = {'base_definition':{'id':state['definition_id'],'version':state['patched_version']},
                    'target_definition':{'id':state['definition_id'],'version':state['patched_version']+100}}; code=404
        elif index == 7: body={'input':{'question':'Is the release ready?'}}; code=202
        elif index == 12: body={'content':'this endpoint only accepts HumanInput nodes'}; code=400
        elif index == 13: body={'reason':'E2E fixture completed'}
        elif index == 15: code=409
        return row['method'],path,body,code,row['actor']

    def _wire(self, operation, row):
        self._active()
        require(self.state['operations'][-1] is row and row['status']=='INTENDED'
                and row['request_id'] not in self._claims
                and [r['operation'] for r in self.state['operations']] == list(ORDER[:len(self.state['operations'])]),
                'Structured request is repeated or out of order')
        method,path,body,expected,actor = self._spec(operation)
        require((row['operation'],row['method'],row['path'],row['actor'],row['body_digest']) ==
                (operation,method,path,actor,fingerprint(body)), 'Structured request binding differs')
        if operation == REQUIREMENTS[-1]: self._admit_delete()
        self._claims.add(row['request_id'])
        headers = {'Content-Type':'application/json','X-Request-ID':row['request_id']}
        if actor != 'HUMAN': headers['Authorization']='Bearer '+self.identities._entries[actor][1]
        connection = HTTPConnection('127.0.0.1',self.port,timeout=5)
        try:
            deadline=time.monotonic()+5
            connection.request(method,path,body=json.dumps(body).encode() if body is not None else None,headers=headers)
            sock=connection.sock; response=connection.getresponse(); raw=bytearray()
            with response:
                while True:
                    remaining=deadline-time.monotonic();require(remaining>0,'Structured response deadline')
                    if sock is not None:sock.settimeout(remaining)
                    chunk=response.read1(min(4096,262145-len(raw)))
                    if not chunk:break
                    raw.extend(chunk);require(len(raw)<=262144,'Structured response byte budget')
                require(response.length in (None,0),'Structured response truncated')
                code=response.status
            return code,json.loads(raw.decode(),object_pairs_hook=unique_keys)
        finally: connection.close()

    def _call(self, operation):
        self._active();method,path,body,expected,actor=self._spec(operation)
        row={'operation':operation,'method':method,'path':path,'actor':actor,'body_digest':fingerprint(body),
             'request_id':'e2e-structured-'+uuid.uuid4().hex,'status':'INTENDED','transitions':['INTENDED']}
        self.state['operations'].append(row);self._save()
        try:
            code,value=self._wire(operation,row);row['http_status']=code
            require(code==expected,'Structured unexpected HTTP status')
            observed=self._observe(operation,value)
            row.update(status='VERIFIED',observed=observed);row['transitions'].append('VERIFIED')
            if operation in REQUIREMENTS:self.state['assertions'].append({'id':operation,'status':'PASS'})
            self._save();return value
        except BaseException:
            row['status']='UNKNOWN';row['transitions'].append('UNKNOWN')
            self.state['unknown_operations'].append(operation);self._save();raise

    def _observe(self, operation, value):
        state=self.state
        if operation in ('fixture-public','fixture-restore'):
            require(isinstance(value,dict) and value.get('success') is True and value.get('data',{}).get('bot_uuid')==self.pm
                    and value['data'].get('visibility') == ('public' if operation=='fixture-public' else 'protected'))
            return {'visibility':value['data']['visibility']}
        if operation in ('implicit-read','implicit-terminal','implicit-cancel'):
            return self._run_view(value,state['implicit']['run_id'],terminal=operation!='implicit-read')
        index=REQUIREMENTS.index(operation)
        if index==0:
            require(isinstance(value,dict) and isinstance(value.get('templates'),list))
            ids=[r['id'] for r in value['templates']];require(TEMPLATE in ids)
            return {'template_ids':ids}
        if index==1:
            expected=(self.root/'templates/zh-CN/single-bot-guided-answer.yaml').read_text()
            require(value.get('id')==TEMPLATE and value.get('lang')=='zh-CN' and value.get('yaml')==expected and expected)
            return {'id':TEMPLATE,'lang':'zh-CN','yaml_sha256':file_sha256(self.root/'templates/zh-CN/single-bot-guided-answer.yaml')}
        if index==2:
            group=identifier(value.get('id'),'created Group');session=identifier(value.get('initial_session_id'),'initial Session')
            require(value.get('driver_bot')==self.ceo and set(value.get('participants',[]))=={self.ceo,self.pm})
            state.update(group_id=group,initial_session_id=session)
            state['effects']['group'].update(status='ADMITTED',id=group)
            return {'group_id':group,'initial_session_id':session,'driver_bot':self.ceo,'participants':[self.ceo,self.pm]}
        if index==3:
            require(value.get('group_strategy')=='state_machine')
            return {'group_strategy':'state_machine'}
        if index in (4,5):
            definition=value.get('default_definition',{});did=identifier(definition.get('id'),'definition')
            version=definition.get('version');require(type(version) is int and version>0)
            if index==4:
                require(value.get('yaml_source')=='original')
                require(did==state['implicit']['definition_id'] and version==state['implicit']['definition_version'])
                state.update(definition_id=did,base_version=version)
            else:
                require(did==state['definition_id'] and version==state['base_version']+1 and REVISED in json.dumps(value))
                state['patched_version']=version
            return {'id':did,'version':version,'yaml_source':value.get('yaml_source'),'revised_instruction':index==5}
        if index==6:
            text=json.dumps(value);require(state['definition_id'] in text and '@'+str(state['patched_version']+100) in text)
            return {'definition_id':state['definition_id'],'missing_version':state['patched_version']+100,'rejected':True}
        if index==7:
            run=value.get('run',{});rid=identifier(run.get('run_id'),'explicit Run')
            require(rid!=state['implicit']['run_id'])
            observed=self._run_view(value,rid)
            require(run.get('session_id')!=state['initial_session_id'] and run.get('definition_id')==state['definition_id']
                    and run.get('definition_version')==state['patched_version'])
            state['explicit']={k:run[k] for k in ('run_id','group_id','session_id','definition_id','definition_version')}
            state['effects']['explicit'].update(status='ADMITTED',**state['explicit'])
            return observed
        if index in (8,9,13,14):return self._run_view(value,state['explicit']['run_id'],terminal=index in (13,14))
        if index==10:
            node=value.get('node',{});require(node.get('node_id')=='answer' and node.get('run_id')==state['explicit']['run_id'])
            return {'node_id':'answer','run_id':state['explicit']['run_id']}
        if index==11:require(value==[]);return []
        if index==12:
            require('not a human_input node' in json.dumps(value));return {'non_human_node_rejected':True}
        if index==15:
            require('must be failed before rerun' in json.dumps(value));return {'aborted_rerun_rejected':True}
        require(index==16 and isinstance(value,dict))
        return {'delete_accepted':True,'group_id':state['group_id']}

    def _run_view(self,value,rid,terminal=False):
        require(isinstance(value,dict));run=value.get('run',{});nodes=value.get('nodes',[])
        require(run.get('run_id')==rid and run.get('group_id')==self.state['group_id']
                and isinstance(run.get('status'),str) and run['status']
                and len(nodes)==1 and nodes[0].get('node_id')=='answer')
        identifier(run.get('session_id'),'run Session')
        if terminal:require(run['status']=='aborted')
        return {'run_id':rid,'group_id':run['group_id'],'session_id':run['session_id'],
                'status':run['status'],'node_id':'answer'}

    def _admit_delete(self):
        require(not self.state['unknown_operations'] and self.state['effects']['group'].get('id')==self.state['group_id'])
        created=next(row for row in self.state['operations'] if row['operation']==REQUIREMENTS[2])
        require(created['status']=='VERIFIED' and created['observed']['group_id']==self.state['group_id'])
        require(self.state['assertions']==[{'id':r,'status':'PASS'} for r in REQUIREMENTS[:-1]])
        from .local_structured_storage import OwnedLocalStructuredStorage
        storage=OwnedLocalStructuredStorage(self.runtime)
        for name in ('implicit','explicit'):
            effect=self.state['effects'][name];require(effect['status']=='CANCELLED')
            observation=storage.run(effect['run_id'])
            require(observation['run']['status']=='aborted' and observation['run']['group_id']==self.state['group_id'])
            require(observation==self.state['terminal_evidence'][name], 'Structured terminal state drift')

    def _terminal(self, name):
        from .local_structured_storage import OwnedLocalStructuredStorage
        proof=OwnedLocalStructuredStorage(self.runtime).run(self.state[name]['run_id'])
        require(proof['run']['status']=='aborted' and proof['run']['group_id']==self.state['group_id'])
        require(proof['session']['status']=='completed' and proof['session'].get('error_message')=='aborted', 'service Session is not terminal')
        self.state['terminal_evidence'][name]=proof
        self.state['effects'][name]['status']='CANCELLED';self._save()

    def run(self):
        require(not self._attempted);self._attempted=True;self._active()
        self.state={'schema_version':1,'run_id':self.run_id,'case_id':CASE_ID,'status':'RUNNING',
            'executor_digest':self.source,'binding_digest':fingerprint(self.binding),
            'operations':[],'assertions':[],'unknown_operations':[],'fixture_state':{},'terminal_evidence':{},
            'effects':{'group':{'status':'NOT_STARTED'},'implicit':{'status':'NOT_STARTED'},'explicit':{'status':'NOT_STARTED'}},
            'resource_disposition':'UNVERIFIED'}
        _exclusive(self.path,self.state);self._digest=fingerprint(self.state)
        from .local_structured_storage import OwnedLocalStructuredStorage
        from .local_structured_receiver import OwnedLocalStructuredReceiver
        storage=OwnedLocalStructuredStorage(self.runtime);receiver=None
        try:
            self.state['fixture_state']['before']=fixture_snapshot(self);self._save()
            with OwnedLocalStructuredReceiver(self.identities) as receiver:
                self._call('fixture-public')
                self._call(REQUIREMENTS[0]);self._call(REQUIREMENTS[1])
                self.state['effects']['group']['status']='INTENDED'
                self.state['effects']['implicit']['status']='INTENDED';self._save()
                self._call(REQUIREMENTS[2])
                initial=storage.group_runs(self.state['group_id']);require(len(initial)==1)
                implicit=initial[0];require(implicit['session_id']==self.state['initial_session_id'])
                self.state['implicit']=implicit
                self.state['effects']['implicit'].update(implicit,status='ADMITTED');self._save()
                view=self._call('implicit-read')
                self.state['implicit_delivery']=receiver.receive_run(run_id=implicit['run_id'],session_id=implicit['session_id'],
                    group_id=self.state['group_id'],attempt=view['nodes'][0]['attempt'],instruction=INSTRUCTION);self._save()
                for operation in REQUIREMENTS[3:7]:self._call(operation)
                self.state['effects']['explicit']['status']='INTENDED';self._save()
                view=self._call(REQUIREMENTS[7]);explicit=self.state['explicit']
                self.state['explicit_delivery']=receiver.receive_run(run_id=explicit['run_id'],session_id=explicit['session_id'],
                    group_id=self.state['group_id'],attempt=view['nodes'][0]['attempt'],instruction=REVISED);self._save()
                for operation in REQUIREMENTS[8:13]:self._call(operation)
                self.state['effects']['explicit']['status']='CANCEL_REQUESTED';self._save()
                self._call(REQUIREMENTS[13]);self._call(REQUIREMENTS[14]);self._terminal('explicit')
                self._call(REQUIREMENTS[15])
                self.state['effects']['implicit']['status']='CANCEL_REQUESTED';self._save()
                self._call('implicit-cancel');self._call('implicit-terminal');self._terminal('implicit')
                self._admit_delete();self.state['effects']['group']['status']='DELETE_REQUESTED';self._save()
                self._call(REQUIREMENTS[16])
                self.state['retention']=storage.group_deleted(self.state['group_id'],
                    [self.state['implicit']['run_id'],explicit['run_id']],self.state['definition_id'])
                self.state['effects']['group']['status']='DELETED';self._save()
                self._call('fixture-restore')
            self.state['receiver']=receiver.summary()
            after=fixture_snapshot(self);require(after==self.state['fixture_state']['before'],'Structured fixture drift')
            self.state['fixture_state'].update(after=after,preserved=True)
            self.state.update(status='LOCAL_STRUCTURED_PASS',resource_disposition='GROUP_SESSIONS_DELETED_HISTORY_RETAINED')
        except BaseException as error:
            self.state.update(status='BLOCKED',error_type=type(error).__name__)
            for effect in self.state['effects'].values():
                if effect['status']=='INTENDED':effect['status']='UNKNOWN'
            raise
        finally:
            if receiver is not None:self.state['receiver']=receiver.summary()
            self._save()
        return copy.deepcopy(self.state)
