"""Finite disabled-bridge Story: never delete/adopt any actual binding."""
import copy
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import time
import uuid
from urllib.parse import quote, urlsplit

from .inventory import current_manifest, executor_digest
from .local_channel_contract import CASE_ID, SUITE, REQUIREMENTS, channel_source_binding
from .local_network_reporting import _exclusive
from .local_provider_storage import OwnedLocalProviderStorage
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import fingerprint, unique_keys
from .run_state import RunStateError, read, write

ROLES = ('CEO','PM','ENG','QA')
TABLES = ('bcs_channel_bindings','bcs_channel_conversations','bcs_channel_im_participants')


def require(value, message='Channel finite contract differs'):
    if not value:
        raise RunStateError(message)


class OwnedLocalChannelHttp:
    def __init__(self, identities):
        require(type(identities) is OwnedLocalRoleIdentity and identities.fixture_set == 'workspace'
                and identities.roles == ROLES and set(identities._verified) == set(ROLES),
                'Channel requires freshly verified owned workspace identities')
        identities._require_roles(ROLES)
        self.identities, self.runtime = identities, identities.runtime
        require(type(self.runtime) is OwnedBcsProcess and self.runtime.provider_sink is None)
        self.root, self.run_id = self.runtime.root, self.runtime.run_id
        self.path = self.root/'local-channel-http.json'
        self.binding_path = self.root/'channel-http-binding.json'
        self.source = executor_digest(); self.source_binding = channel_source_binding()
        self.binding = read(self.binding_path)
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.config_digest = file_sha256(self.runtime.inputs.config_path)
        target = urlsplit(identities.origin)
        require(target.scheme == 'http' and target.hostname == '127.0.0.1' and target.port
                and target.netloc == '127.0.0.1:'+str(target.port) and not target.path
                and not target.query and not target.fragment)
        self.port = target.port
        self.missing = 'missing-channel-'+hashlib.sha256(self.run_id.encode()).hexdigest()[:24]
        self.state = None; self._digest = ''; self._attempted = False; self._claims = set()
        self._active()
        require(not self.path.exists() and not self.path.is_symlink(), 'Channel journal already exists')

    def _active(self):
        self.identities._require_roles(ROLES)
        require(self.runtime.root == self.root and self.runtime.run_id == self.run_id
                and executor_digest() == self.source and channel_source_binding() == self.source_binding
                and file_sha256(self.runtime.inputs.config_path) == self.config_digest
                and fingerprint(read(self.identities.fixtures.path)) == self.fixture_digest)
        expected = {'schema_version':1, 'contract':SUITE, 'suite':SUITE, 'case_id':CASE_ID,
                    'run_id':self.run_id, 'executor_digest':self.source,
                    'bcs_binary_sha256':self.runtime.binary_sha256, 'source_binding':self.source_binding,
                    'selected_requirement_ids':list(REQUIREMENTS), 'legacy_inventory':current_manifest()['legacy_inventory'],
                    'runtime_dir':str(self.root.resolve()), 'store_messages':False}
        require(self.binding == expected and read(self.binding_path) == expected and not self.binding_path.is_symlink())
        config = json.loads(self.runtime.inputs.config_path.read_text())
        require(config.get('channel', {}) == {} and config.get('store_messages') is False
                and config.get('eventing') == {'enabled':False,'dispatcher_enabled':False}
                and config.get('human_notify') == {'providers':[]}
                and config.get('security',{}).get('outbound_url') == {'block_private_networks':True,'allow_loopback':False})
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest, 'Channel evidence changed')

    def _save(self):
        if self._digest:
            require(fingerprint(read(self.path)) == self._digest, 'Channel evidence changed')
        raw = json.dumps(self.state)
        require(not any(token in raw for _,token in self.identities._entries.values()), 'credential in Channel evidence')
        write(self.path,self.state); self._digest = fingerprint(self.state)

    def _snapshot(self):
        self._active()
        storage = OwnedLocalProviderStorage(self.runtime)
        fixtures = storage.fixture_snapshot(self.identities._entries,self.identities.human)
        with storage._connection() as db:
            db.execute('BEGIN')
            absent = {}
            for table in TABLES:
                # Fixed local-only tables, LIMIT1 bounds work and output; never arbitrary SQL.
                absent[table] = db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() is None
        require(all(absent.values()), 'Channel database is not empty; no mutations admitted')
        return {'fixtures':fixtures,'empty_tables':absent,'source':'OWNED_LOCAL_SQLITE'}

    def _spec(self, index):
        ceo = self.identities._entries['CEO'][0]
        return (
            ('POST','/channels/bindings',{'channel_type':'e2e-uninstalled-channel',
                'account_ref':'account-'+self.run_id,'target':{'bot':{'bot_id':ceo}},
                'outbound_visibility':'full_transcript','env':'local','config':{'mode':'story'}},400),
            ('GET','/channels/bindings',None,200),
            ('GET','/channels/bindings/by-target?target_type=bot&target_id='+quote(ceo,safe='')+'&channel_type=e2e-uninstalled-channel',None,200),
            ('PATCH','/channels/bindings/'+self.missing,{'active':False},200),
            ('DELETE','/channels/bindings/'+self.missing,None,200),
            ('GET','/channels/bindings',None,200))[index]

    def _wire(self, index, row):
        self._active()
        require(self.state is not None and len(self.state['operations']) == index+1
                and self.state['operations'][-1] is row and row['status'] == 'INTENDED'
                and row['submitted_request_id'] not in self._claims)
        method,path,body,status = self._spec(index)
        require(self.state['assertions'] == [{'id':x,'status':'PASS'} for x in REQUIREMENTS[:index]]
                and [r['operation'] for r in self.state['operations']] == list(REQUIREMENTS[:index+1]))
        if method in ('PATCH','DELETE'):
            require(self.state['no_op_admissions'] and self.state['no_op_admissions'][-1] == {
                'operation':REQUIREMENTS[index],'binding_id':self.missing,
                'basis':'DISABLED_BRIDGE_AND_EMPTY_OWNED_DATABASE','snapshot':self.state['fixture_state']['before']})
        require(row['operation'] == REQUIREMENTS[index] and row['method'] == method and row['path'] == path)
        self._claims.add(row['submitted_request_id'])
        connection = HTTPConnection('127.0.0.1',self.port,timeout=5)
        try:
            deadline = time.monotonic()+5
            connection.request(method,path,body=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type':'application/json','X-Request-ID':row['submitted_request_id']})
            sock = connection.sock; response = connection.getresponse(); raw = bytearray()
            with response:
                while True:
                    remaining = deadline-time.monotonic(); require(remaining>0,'Channel response deadline exceeded')
                    if sock is not None: sock.settimeout(remaining)
                    chunk = response.read1(min(4096,65537-len(raw)))
                    if not chunk: break
                    raw.extend(chunk); require(len(raw)<=65536,'Channel response exceeds budget')
                require(response.length in (None,0),'Channel response truncated')
                code = response.status
            return code,json.loads(raw.decode(),object_pairs_hook=unique_keys)
        finally:
            connection.close()

    def run(self):
        require(not self._attempted,'Channel execution cannot repeat'); self._attempted = True
        self._active()
        self.state = {'schema_version':1,'run_id':self.run_id,'case_id':CASE_ID,'status':'RUNNING',
            'executor_digest':self.source,'binding_digest':fingerprint(self.binding),
            'source_binding_digest':fingerprint(self.source_binding),'operations':[],'assertions':[],
            'missing_binding_id':self.missing,'fixture_state':{},'no_op_admissions':[],
            'resources':{'created':[],'deleted':[],'unknown':[]},'resource_disposition':'UNVERIFIED'}
        _exclusive(self.path,self.state); self._digest = fingerprint(self.state)
        try:
            before = self._snapshot(); self.state['fixture_state']['before'] = before; self._save()
            for index,requirement in enumerate(REQUIREMENTS):
                self._active(); method,path,body,expected = self._spec(index)
                if method in ('PATCH','DELETE'):
                    require(self.state['assertions'] == [{'id':x,'status':'PASS'} for x in REQUIREMENTS[:index]])
                    observed = self._snapshot(); require(observed == before)
                    self.state['no_op_admissions'].append({'operation':requirement,'binding_id':self.missing,
                        'basis':'DISABLED_BRIDGE_AND_EMPTY_OWNED_DATABASE','snapshot':observed})
                row = {'operation':requirement,'method':method,'path':path,
                    'submitted_request_id':'e2e-channel-'+uuid.uuid4().hex,'status':'INTENDED','transitions':['INTENDED']}
                self.state['operations'].append(row); self._save()
                try:
                    code,value = self._wire(index,row); row['http_status'] = code
                    require(code == expected and isinstance(value,dict),'Channel response contract differs')
                    if index == 0:
                        require(value.get('code') == 'bad_request' and 'channel bridge is disabled' in json.dumps(value))
                        observed = {'code':'bad_request','disabled_bridge':True}
                    elif index in (3,4):
                        require(value == {'ok':True}); observed = value
                    else:
                        require(value == {'items':[]}); observed = value
                    row.update(status='VERIFIED',observed=observed); row['transitions'].append('VERIFIED')
                    self.state['assertions'].append({'id':requirement,'status':'PASS'}); self._save()
                except BaseException:
                    row['status'] = 'UNKNOWN'; row['transitions'].append('UNKNOWN')
                    if method in ('POST','PATCH','DELETE'):
                        self.state['resources']['unknown'].append({'operation':requirement,'request_id':row['submitted_request_id']})
                    raise
            after = self._snapshot(); require(after == before)
            self.state['fixture_state'].update(after=after,preserved=True)
            self.state.update(status='LOCAL_CHANNEL_HTTP_PASS',resource_disposition='NO_BINDING_CREATED_NO_RESOURCE_DELETED')
        except BaseException as error:
            self.state.update(status='BLOCKED',error_type=type(error).__name__)
            if isinstance(error,(KeyboardInterrupt,SystemExit)): raise
            raise RunStateError('Channel Story blocked; no retry or cleanup') from None
        finally:
            self._save()
        return copy.deepcopy(self.state)
