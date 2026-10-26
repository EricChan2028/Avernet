"""Original Event Webhook journey, with one private receiver and exact effects."""
import base64
import copy
import hashlib
import hmac
from http.client import HTTPConnection
import json
import time
import uuid
from urllib.parse import quote,urlsplit

from .inventory import executor_digest
from .local_event_contract import CASE_ID,SUITE,REQUIREMENTS,event_source_binding
from .local_event_sink import OwnedLocalEventSink
from .local_network_reporting import _exclusive
from .local_provider_storage import OwnedLocalProviderStorage
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess,file_sha256
from .profile import fingerprint,identifier,unique_keys
from .run_state import RunStateError,read,write

PREFIX='/openapi/v1/collaboration'
MESSAGE='Webhook delivery verification'
HTTP_IDS=tuple(REQUIREMENTS[i] for i in (2,4,5,6,8,9,10,12,15,16,17,18,19))
EVENTS={REQUIREMENTS[3]:'group.created',REQUIREMENTS[7]:'event_subscription.test',
        REQUIREMENTS[11]:'session.created',REQUIREMENTS[13]:'message.created'}


def require(value,message='Event Webhook finite contract differs'):
    if not value:raise RunStateError(message)


class OwnedLocalEventStory:
    ROLES=('CEO','PM','ENG','QA')

    def __init__(self,identities,sink):
        require(type(identities) is OwnedLocalRoleIdentity and identities.fixture_set=='workspace'
                and identities.roles==self.ROLES and set(identities._verified)==set(self.ROLES))
        identities._require_roles(self.ROLES)
        self.identities=identities;self.runtime=identities.runtime;self.sink=sink
        require(type(self.runtime) is OwnedBcsProcess and type(sink) is OwnedLocalEventSink
                and self.runtime.event_sink is sink and self.runtime.store_messages is True)
        self.root=self.runtime.root;self.run_id=self.runtime.run_id;sink.validate_live(self.run_id)
        self.url=sink.url;self.source=executor_digest();self.source_binding=event_source_binding()
        self.binding=read(self.root/'event-binding.json')
        require(self.binding['run_id']==self.run_id and self.binding['case_id']==CASE_ID
                and self.binding['executor_digest']==self.source and self.binding['source_binding']==self.source_binding
                and self.binding['selected_requirement_ids']==list(REQUIREMENTS))
        self.config_digest=file_sha256(self.runtime.inputs.config_path)
        self.fixture_digest=fingerprint(read(identities.fixtures.path))
        target=urlsplit(identities.origin)
        require(target.scheme=='http' and target.netloc=='127.0.0.1:'+str(target.port)
                and not target.path and not target.query and not target.fragment)
        self.port=target.port;self.pm=identities._entries['PM'][0];self.eng=identities._entries['ENG'][0]
        self.path=self.root/'local-event.json';self.state=None;self._digest='';self._claims=set()
        self._attempted=False;self._principal=''
        require(not self.path.exists() and not self.path.is_symlink())
        self._active()

    def _active(self):
        self.identities._require_roles(self.ROLES);self.sink.validate_live(self.run_id)
        require(self.sink.url==self.url and self.runtime.root==self.root
                and executor_digest()==self.source and read(self.root/'event-binding.json')==self.binding
                and file_sha256(self.runtime.inputs.config_path)==self.config_digest
                and fingerprint(read(self.identities.fixtures.path))==self.fixture_digest)
        if self._digest:require(fingerprint(read(self.path))==self._digest,'Event journal drift')

    def _save(self):
        if self._digest:require(fingerprint(read(self.path))==self._digest)
        text=json.dumps(self.state)
        require(not any(token in text for _,token in self.identities._entries.values()))
        require(not self._principal or self._principal not in text)
        require(self.url not in text,'receiver capability URL in journal')
        write(self.path,self.state);self._digest=fingerprint(self.state)

    def _principal_once(self):
        self._active();require(not self._principal)
        now=int(time.time());key=self.runtime.inputs.environment['AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE']
        claims={'iss':'gateway','aud':'bcs','iat':now,'exp':now+60,
            'principals':[{'type':'user','tenant':'e2e-local','subject':{'id':self.identities.human,
                'username':'E2E local test human','display_name':None,'full_name':None,'tenant_id':'e2e-local'}}]}
        def encode(value):return base64.urlsafe_b64encode(value).rstrip(b'=').decode()
        unsigned='.'.join(encode(json.dumps(value,sort_keys=True,separators=(',',':')).encode())
                          for value in ({'alg':'HS256','kid':'bare','typ':'JWT'},claims))
        self._principal=unsigned+'.'+encode(hmac.new(key.encode(),unsigned.encode(),hashlib.sha256).digest())
        return {'identity':'OWNED_LOCAL_HUMAN_PRINCIPAL','human_id':self.identities.human,'credential_persisted':False}

    def _spec(self,operation):
        require(operation in HTTP_IDS)
        row=self.source_binding['requirements'][REQUIREMENTS.index(operation)]
        path=row['path'].replace('${public_prefix}',PREFIX)
        for name in ('group_id','subscription_id','session_id','delivery_id'):
            literal='${'+name+'}'
            if literal in path:
                path=path.replace(literal,quote(identifier(self.state.get(name),name),safe=''))
        if '$(urlencode "$group_id")' in path:
            path=path.replace('$(urlencode "$group_id")',quote(identifier(self.state.get('group_id'),'Group'),safe=''))
        body=None;code=200
        if operation==REQUIREMENTS[2]:
            code=201;body={'group_kind':'normal','name':'Event Webhook E2E','context':'Observe collaboration lifecycle events',
                'driver_bot_uuid':self.pm,'participants':[{'actor_id':self.pm,'role':'driver'},{'actor_id':self.eng,'role':'consultant'}],
                'collaboration':{'strategy':'chat','delivery_policy':{'bot_final_delivery':'send_to_driver'}},
                'event_subscriptions':[{'name':'E2E lifecycle observer','event_filters':['group.*','session.*','message.*'],
                    'payload':{'mode':'full'},'sink':{'type':'webhook','url':self.url,'request_timeout_ms':5000}}]}
        elif operation==REQUIREMENTS[8]:body={'revision':1,'name':'E2E lifecycle observer paused','status':'disabled'}
        elif operation==REQUIREMENTS[9]:body={'revision':2,'name':'E2E lifecycle observer','status':'active'}
        elif operation==REQUIREMENTS[10]:code=201;body={'title':'Event Webhook E2E session','input':{'source':'e2e'}}
        elif operation==REQUIREMENTS[12]:body={'sender':self.pm,'content':MESSAGE,'message_type':'bot','role':'user'}
        return row['method'],path,body,code,row['actor']

    def _wire(self,operation,row):
        self._active();method,path,body,expected,actor=self._spec(operation)
        require(row is self.state['operations'][-1] and row['status']=='INTENDED' and row['request_id'] not in self._claims
                and row['operation']==REQUIREMENTS[len(self.state['assertions'])]
                and [r['operation'] for r in self.state['operations']]==list(HTTP_IDS[:len(self.state['operations'])]))
        require((row['method'],row['path'],row['actor'],row['body_digest'])==(method,path,actor,fingerprint(body)))
        if method=='DELETE':self._admit_delete(operation)
        self._claims.add(row['request_id']);headers={'Content-Type':'application/json','X-Request-ID':row['request_id']}
        if actor=='HUMAN_PRINCIPAL':
            require(self._principal);headers['X-Avernet-Principal']=self._principal
        connection=HTTPConnection('127.0.0.1',self.port,timeout=5)
        try:
            deadline=time.monotonic()+5
            connection.request(method,path,body=json.dumps(body).encode() if body is not None else None,headers=headers)
            sock=connection.sock;response=connection.getresponse();raw=bytearray()
            with response:
                while True:
                    remaining=deadline-time.monotonic();require(remaining>0,'Event API deadline exceeded')
                    if sock is not None:sock.settimeout(remaining)
                    part=response.read1(min(4096,262145-len(raw)))
                    if not part:break
                    raw.extend(part);require(len(raw)<=262144,'Event API byte limit')
                require(response.length in (None,0),'Event API response truncated');code=response.status
            return code,json.loads(raw.decode(),object_pairs_hook=unique_keys)
        finally:connection.close()

    def _call(self,operation):
        self._active();method,path,body,expected,actor=self._spec(operation)
        row={'operation':operation,'method':method,'path':path,'actor':actor,'body_digest':fingerprint(body),
             'request_id':'e2e-event-'+uuid.uuid4().hex,'status':'INTENDED','transitions':['INTENDED']}
        self.state['operations'].append(row);self._save()
        try:
            code,value=self._wire(operation,row);row['http_status']=code;require(code==expected,'Unexpected Event HTTP status')
            row['observed']=self._observe(operation,value);row['status']='VERIFIED';row['transitions'].append('VERIFIED')
            self._save();return row['observed']
        except BaseException:
            row['status']='UNKNOWN';row['transitions'].append('UNKNOWN');self.state['unknown_operations'].append(operation)
            self._save();raise

    def _observe(self,operation,value):
        require(isinstance(value,dict))
        data=value.get('data') if operation!=REQUIREMENTS[12] else value
        require(isinstance(data,dict),'Event API envelope differs')
        if operation==REQUIREMENTS[2]:
            gid=identifier(data.get('group_id'),'created Group');subs=data.get('event_subscriptions',[]);require(len(subs)==1)
            sid=identifier(subs[0].get('subscription_id'),'inline Subscription')
            self.state.update(group_id=gid,subscription_id=sid)
            self.state['effects']['group'].update(id=gid,status='ADMITTED')
            self.state['effects']['subscription'].update(id=sid,status='ADMITTED')
            return {'group_id':gid,'subscription_id':sid}
        if operation==REQUIREMENTS[4]:
            require(data.get('items') and data['items'][0].get('subscription_id')==self.state['subscription_id'])
            return {'subscription_id':self.state['subscription_id']}
        if operation in (REQUIREMENTS[5],REQUIREMENTS[8],REQUIREMENTS[9],REQUIREMENTS[17]):
            revision,status={REQUIREMENTS[5]:(1,'active'),REQUIREMENTS[8]:(2,'disabled'),REQUIREMENTS[9]:(3,'active'),REQUIREMENTS[17]:(4,'deleted')}[operation]
            require(data.get('revision')==revision and data.get('status')==status)
            return {'subscription_id':self.state['subscription_id'],'revision':revision,'status':status}
        if operation==REQUIREMENTS[6]:require(data.get('delivered') is True);return {'delivered':True}
        if operation==REQUIREMENTS[10]:
            sid=identifier(data.get('session_id'),'explicit Session');require(sid not in self.state['session_ids'])
            self.state['session_id']=sid;self.state['session_ids'].append(sid)
            self.state['effects']['explicit_session'].update(id=sid,status='ADMITTED')
            return {'session_id':sid,'group_id':self.state['group_id']}
        if operation==REQUIREMENTS[12]:
            mid=identifier(data.get('message_id'),'persisted Message')
            self.state['message_id']=mid;self.state['effects']['message']['id']=mid
            return {'message_id':mid,'message_accepted':True,'content_sha256':hashlib.sha256(MESSAGE.encode()).hexdigest()}
        if operation==REQUIREMENTS[15]:
            require(data.get('items'));did=identifier(data['items'][0].get('delivery_id'),'successful Delivery')
            self.state['delivery_id']=did;return {'delivery_id':did}
        if operation==REQUIREMENTS[16]:
            require(data.get('delivery',{}).get('subscription_id')==self.state['subscription_id'])
            return {'delivery_id':self.state['delivery_id'],'subscription_id':self.state['subscription_id']}
        if operation==REQUIREMENTS[18]:return {'session_id':self.state['session_id'],'delete_accepted':True}
        require(operation==REQUIREMENTS[19]);return {'group_id':self.state['group_id'],'delete_accepted':True}

    def _admit_delete(self,operation):
        require(not self.state['unknown_operations'])
        group=next(row for row in self.state['operations'] if row['operation']==REQUIREMENTS[2])
        require(group['status']=='VERIFIED' and group['observed']=={'group_id':self.state['group_id'],'subscription_id':self.state['subscription_id']})
        require(self.state['effects']['group']['id']==self.state['group_id'])
        if operation in (REQUIREMENTS[18],REQUIREMENTS[19]):
            require(self.state['effects']['subscription']['status']=='TOMBSTONED')
        if operation==REQUIREMENTS[18]:
            require(self.state['effects']['explicit_session']['id']==self.state['session_id']
                    and len(self.state['session_ids'])==2)
        if operation==REQUIREMENTS[19]:require(self.state['effects']['explicit_session']['status']=='DELETED')

    def run(self):
        require(not self._attempted);self._attempted=True;self._active()
        self.state={'schema_version':1,'run_id':self.run_id,'case_id':CASE_ID,'status':'RUNNING',
            'executor_digest':self.source,'binding_digest':fingerprint(self.binding),'assertions':[],'observations':{},
            'operations':[],'unknown_operations':[],'session_ids':[],'fixture_state':{},'resource_disposition':'UNVERIFIED',
            'effects':{name:{'status':'NOT_STARTED'} for name in ('group','subscription','implicit_session','explicit_session','message')}}
        _exclusive(self.path,self.state);self._digest=fingerprint(self.state)
        from .local_event_storage import OwnedLocalEventStorage
        storage=OwnedLocalEventStorage(self.runtime)
        try:
            before=OwnedLocalProviderStorage(self.runtime).fixture_snapshot(self.identities._entries,self.identities.human)
            self.state['fixture_state']['before']=before;self._save()
            for operation in REQUIREMENTS:
                self._active();require(self.state['assertions']==[{'id':key,'status':'PASS'} for key in REQUIREMENTS[:len(self.state['assertions'])]])
                if operation==REQUIREMENTS[0]:
                    observed=self.sink.snapshot();require(observed['state']=='LIVE' and observed['received']==0 and not observed['armed'] and not observed['failed'])
                elif operation==REQUIREMENTS[1]:observed=self._principal_once()
                elif operation in EVENTS:
                    subject={REQUIREMENTS[3]:self.state['group_id'],REQUIREMENTS[7]:self.state['subscription_id'],
                             REQUIREMENTS[11]:self.state.get('session_id'),REQUIREMENTS[13]:self.state.get('logical_message_id')}[operation]
                    identifier(subject,'expected callback subject')
                    observed=self.sink.wait_for(EVENTS[operation],self.state['group_id'],time.monotonic()+5,subject_id=subject)
                    require(observed['authorization'] is None)
                elif operation==REQUIREMENTS[14]:
                    observed=self.sink.snapshot();require(not observed['failed'] and observed['bound'] and observed['buffered']==0)
                    streams={}
                    for event in observed['records']:
                        streams.setdefault(event['stream_key'],[]).append(event['sequence'])
                    require(streams and all(all(type(v) is int for v in seq) and seq==sorted(set(seq)) for seq in streams.values()))
                    observed={'streams':streams,'record_count':len(observed['records'])}
                else:
                    if operation==REQUIREMENTS[2]:
                        for name in ('group','subscription','implicit_session'):self.state['effects'][name]['status']='INTENDED'
                        self._save();self.sink.arm_create_intent(self.run_id)
                    elif operation==REQUIREMENTS[10]:self.state['effects']['explicit_session']['status']='INTENDED';self._save()
                    elif operation==REQUIREMENTS[12]:
                        self.state['message_before']=storage.message(self.state['group_id'],self.state['session_ids'],self.pm,absent=True)
                        self.state['effects']['message']['status']='INTENDED';self._save()
                    elif operation in REQUIREMENTS[17:]:
                        target={REQUIREMENTS[17]:'subscription',REQUIREMENTS[18]:'explicit_session',REQUIREMENTS[19]:'group'}[operation]
                        self._admit_delete(operation);self.state['effects'][target]['status']='DELETE_REQUESTED';self._save()
                    observed=self._call(operation)
                    if operation==REQUIREMENTS[2]:
                        self.sink.bind_group(self.state['group_id'],self.state['subscription_id'])
                        proof=storage.group(self.state['group_id'],self.state['subscription_id']);require(len(proof['sessions'])==1)
                        sid=identifier(proof['sessions'][0]['session_id'],'implicit Session')
                        self.state['session_ids']=[sid];self.state['effects']['implicit_session'].update(status='ADMITTED',id=sid)
                        require(proof['group']['driver_bot']==self.pm and set(proof['group']['member_ids'])=={self.pm,self.eng})
                        self.state['created_evidence']=proof
                    elif operation==REQUIREMENTS[10]:
                        proof=storage.group(self.state['group_id'],self.state['subscription_id'])
                        require({s['session_id'] for s in proof['sessions']}==set(self.state['session_ids']))
                        self.state['sessions_evidence']=proof
                    elif operation==REQUIREMENTS[12]:
                        proof=storage.message(self.state['group_id'],self.state['session_ids'],self.pm)
                        self.state['message_evidence']=proof;self.state['logical_message_id']=proof['message_id']
                        self.state['effects']['message'].update(status='ACCEPTED_HISTORY_RETAINED',logical_message_id=proof['message_id'])
                    elif operation==REQUIREMENTS[16]:self.state['delivery_evidence']=storage.delivery(self.state['delivery_id'],self.state['group_id'],self.state['subscription_id'])
                    elif operation==REQUIREMENTS[17]:self.state['effects']['subscription']['status']='TOMBSTONED'
                    elif operation==REQUIREMENTS[18]:self.state['effects']['explicit_session']['status']='DELETED'
                    elif operation==REQUIREMENTS[19]:
                        self.state['retention']=storage.deleted(self.state['group_id'],self.state['subscription_id'],self.state['session_ids'])
                        self.state['message_retained']=storage.message(self.state['group_id'],self.state['session_ids'],self.pm)
                        require(self.state['message_retained']==self.state['message_evidence'],'Message history changed after deletion')
                        self.state['effects']['group']['status']='DELETED';self.state['effects']['implicit_session']['status']='DELETED_WITH_GROUP'
                self.state['observations'][operation]=observed
                self.state['assertions'].append({'id':operation,'status':'PASS'});self._save()
            after=OwnedLocalProviderStorage(self.runtime).fixture_snapshot(self.identities._entries,self.identities.human)
            require(after==before,'Event fixture drift');self.state['fixture_state'].update(after=after,preserved=True)
            self.state.update(status='LOCAL_EVENT_PASS',resource_disposition='GROUP_SESSIONS_DELETED_EVENT_HISTORY_RETAINED')
        except BaseException as error:
            self.state.update(status='BLOCKED',error_type=type(error).__name__)
            for effect in self.state['effects'].values():
                if effect['status']=='INTENDED':effect['status']='UNKNOWN'
            raise
        finally:
            self._principal='';self.state['receiver']=self.sink.snapshot();self.state['principal_memory']='CLEARED';self._save()
        return copy.deepcopy(self.state)
