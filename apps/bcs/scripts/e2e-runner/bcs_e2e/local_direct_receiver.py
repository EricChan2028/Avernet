"""Exact direct-chat transport receipt, distinct from BCS Group delivery."""
import hashlib
import time

from .local_bot_receiver import OwnedLocalBotReceiver, LocalBotReceiverError
from .profile import identifier


def validate_direct_frame(frame, *, run_id, session_id, caller_id, target_id, text, tags):
    """Validate the original direct-chat frame; never acknowledge execution."""
    for value in (run_id,session_id,caller_id,target_id): identifier(value,'direct chat identity')
    if (not isinstance(text,str) or not text or len(text.encode())>4096
            or not isinstance(tags,list) or len(tags)>8
            or any(not isinstance(t,str) or not t or len(t)>128 for t in tags)):
        raise LocalBotReceiverError('direct-chat expected content exceeds contract')
    params = frame.get('params') if isinstance(frame,dict) else None
    if (not isinstance(params,dict) or frame.get('type')!='req' or frame.get('method')!='chat.send'
            or frame.get('id')!=run_id or params.get('session_key')!=session_id
            or params.get('bcs_group_id')!=session_id or params.get('bcs_session_id') is not None
            or params.get('tags',[])!=tags or params.get('attachments',[])!=[]):
        raise LocalBotReceiverError('direct-chat frame is not the exact admitted Run/session')
    channel,context,message = (params.get(k) for k in ('channel','session_context','message'))
    if (not isinstance(channel,dict) or channel.get('actor_id')!=caller_id
            or channel.get('thread_id') is not None
            or not isinstance(context,dict) or context.get('session_id')!=session_id
            or context.get('recipient')!=target_id or context.get('participants')!=[target_id]
            or context.get('from_bot_id')!=caller_id or context.get('message')!=text
            or not isinstance(message,dict) or message.get('role')!='user'
            or message.get('content')!=[{'type':'text','text':text}]):
        raise LocalBotReceiverError('direct-chat caller/target/content binding differs')
    return {'status':'TRANSPORT_RECEIVED_ONLY','request_id':run_id,'run_id':run_id,
            'session_id':session_id,'caller_id':caller_id,'target_id':target_id,
            'method':'chat.send','text_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'tags':list(tags),'success_ack_sent':False,'engine':'NOT_STARTED',
            'scope':'DIRECT_RUN_NOT_GROUP_SESSION'}


class OwnedLocalDirectReceiver:
    def __init__(self, identities, role='PM'):
        if role!='PM': raise LocalBotReceiverError('direct Story receiver must be owned PM')
        self.receiver=OwnedLocalBotReceiver(identities,role)
        self.identities=identities; self.deliveries=[]; self._entered=False

    def __enter__(self):
        if self._entered: raise LocalBotReceiverError('direct receiver cannot be reopened')
        self._entered=True; self.receiver.__enter__(); return self

    def __exit__(self,*_args): self.close()

    def close(self): self.receiver.close()

    def summary(self):
        return {**self.receiver.summary(),'scope':'DIRECT_RUN_NOT_GROUP_SESSION',
                'engine':'NOT_STARTED','deliveries':[dict(row) for row in self.deliveries]}

    def receive_run(self, *, run_id, session_id, caller_id, text, tags):
        try:
            if (self.receiver._state!='OPEN' or self.receiver._connection is None
                    or len(self.deliveries)>=2 or any(r['run_id']==run_id for r in self.deliveries)):
                raise LocalBotReceiverError('direct receiver request is repeated or outside lifecycle')
            self.identities._require_roles(('CEO','PM'))
            if caller_id!=self.identities._entries['CEO'][0]:
                raise LocalBotReceiverError('direct caller is not the owned CEO')
            deadline=time.monotonic()+5; unrelated=0
            for _ in range(32):
                if time.monotonic()>=deadline: raise LocalBotReceiverError('direct delivery deadline exceeded')
                opcode,raw=self.receiver._connection.receive_frame(deadline)
                if opcode==0x9:
                    self.receiver._connection._send_frame(0xA,raw); continue
                if opcode==0xA: continue
                if opcode!=0x1: raise LocalBotReceiverError('direct delivery was not a text frame')
                frame=self.receiver._decode_frame(raw,'direct delivery')
                if frame.get('type')!='req':
                    unrelated+=1
                    if unrelated>8: raise LocalBotReceiverError('too many unrelated direct frames')
                    continue
                receipt=validate_direct_frame(frame,run_id=run_id,session_id=session_id,
                    caller_id=caller_id,target_id=self.receiver.bot_id,text=text,tags=tags)
                self.deliveries.append(receipt); return dict(receipt)
            raise LocalBotReceiverError('direct delivery frame budget exceeded')
        except BaseException:
            self.close(); raise
