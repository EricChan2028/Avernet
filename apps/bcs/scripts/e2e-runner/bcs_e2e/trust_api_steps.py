"""Legacy trust API business steps through the controlled adapter only.

No raw network, SQL, process operations, token access or environment-specific
credentials are available here. The optional actual CLI continuation uses the
same controlled runtime; remote and release integration remain separate.
"""
from __future__ import annotations

from .profile import identifier
from .trust_baseline import visibility


def execute_trust_steps(case, originals: dict) -> None:
    changed = {}
    for role,desired in (('ENG','public'),('PM','protected'),('CS','protected')):
        current=visibility(case._read(role,'visibility'),case._id(role))
        case._assert(role+'-visibility-baseline',current==originals[role])
        case._visibility_stored(role,current,'initial')
        if current != desired:
            result=case._mutate(role,'visibility.set',desired=desired)
            case._assert(role+'-visibility-updated',visibility(result,case._id(role))==desired)
            changed[role]=desired
        case._assert(role+'-visibility-readback',visibility(case._read(role,'visibility'),case._id(role))==desired)
        case._visibility_stored(role,desired,'target')
    for sender,receiver,terminal in (('CEO','ENG','accepted'),('QA','PM','accepted'),('ENG','CS','rejected')):
        expected='accepted' if receiver=='ENG' else 'pending'
        created=case._mutate(sender,'friend.request',peer=receiver).get('data')
        if not isinstance(created,dict): raise RuntimeError('new request returned no exact identity')
        rid=identifier(created.get('id'),'created friendship request ID')
        case._assert(sender+'-'+receiver+'-created',
                     created.get('from_bot')==case._id(sender) and created.get('to_bot')==case._id(receiver)
                     and created.get('status')==expected and not any(r['request_id']==rid for r in case.state['requests']))
        item={'request_id':rid,'sender':sender,'receiver':receiver,'status':expected}
        case.state['requests'].append(item)
        case.state['operations'][-1]['resource_binding']='BOUND'
        case._save()
        case._persisted(item,expected)
        if expected=='pending':
            case._request_match(item,'pending','sent')
            case._request_match(item,'pending','received')
            case._mutate(receiver,'friend.accept' if terminal=='accepted' else 'friend.reject',request_id=rid)
            item['status']=terminal;case._save()
            case._persisted(item,terminal)
            pending=case._read(receiver,'pending-received').get('data')
            case._assert(sender+'-'+receiver+'-pending-cleared',isinstance(pending,list) and pending==[])
        case._request_match(item,terminal,'sent')
        case._request_match(item,terminal,'received')
        case._friends(sender,receiver,terminal=='accepted')
        case._friends(receiver,sender,terminal=='accepted')
    if case.cli is not None:
        execute_cli_friend_steps(case)
    case._final_storage()
    case.state['functional']='PASS';case._save()
    for role,expected in reversed(list(changed.items())):
        current=visibility(case._read(role,'visibility'),case._id(role))
        if current==originals[role]:
            case._visibility_stored(role,originals[role],'already-restored')
            continue
        case._assert(role+'-restore-no-drift',current==expected)
        case._visibility_stored(role,expected,'restore-guard')
        value=case._mutate(role,'visibility.restore',desired=originals[role])
        case._assert(role+'-restore-response',visibility(value,case._id(role))==originals[role])
        case._assert(role+'-restore-readback',visibility(case._read(role,'visibility'),case._id(role))==originals[role])
        case._visibility_stored(role,originals[role],'restored')
    for role in ('CEO','ENG','PM','QA','CS'):
        case._assert(role+'-final-original-visibility',visibility(case._read(role,'visibility'),case._id(role))==originals[role])
        case._visibility_stored(role,originals[role],'final')

# CEO to PM actual CLI business sequence (merged from trust_cli_steps);
# all effects use the controlled adapter.

def execute_cli_friend_steps(case):
    created = case.cli.execute(case, 'request')
    case._assert('cli-request-output', isinstance(created, dict) and created.get('success') is True)
    data = created.get('data')
    if not isinstance(data, dict):
        raise RuntimeError('CLI returned no exact new friendship request')
    request_id = identifier(data.get('id'), 'CLI created request ID')
    case._assert('CEO-PM-created', data.get('from_bot')==case._id('CEO')
                 and data.get('to_bot')==case._id('PM') and data.get('status')=='pending'
                 and not any(row['request_id']==request_id for row in case.state['requests']))
    item = {'request_id':request_id, 'sender':'CEO', 'receiver':'PM', 'status':'pending'}
    case.state['requests'].append(item)
    case.state['cli_operations'][-1].update(resource_binding='BOUND', response_resource_id=request_id)
    case._save()
    case._persisted(item, 'pending')
    case._request_match(item, 'pending', 'sent')
    case._request_match(item, 'pending', 'received')
    received = case.cli.execute(case, 'requests', request_id=request_id)
    case._assert('cli-received-exact-request', isinstance(received, list) and len(received)==1
                 and received[0].get('id')==request_id and received[0].get('from_bot')==case._id('CEO')
                 and received[0].get('to_bot')==case._id('PM') and received[0].get('status')=='pending')
    accepted = case.cli.execute(case, 'accept', request_id=request_id)
    case._assert('cli-accept-output', accepted=={'success':True})
    item['status']='accepted'; case._save()
    case._persisted(item, 'accepted')
    case._assert('CEO-PM-pending-cleared', case._read('PM', 'pending-received').get('data')==[])
    for direction in ('sent', 'received'):
        case._request_match(item, 'accepted', direction)
    case._friends('CEO', 'PM', True)
    case._friends('PM', 'CEO', True)
    friends = case.cli.execute(case, 'list', request_id=request_id)
    case._assert('cli-ceo-list-includes-pm', isinstance(friends,list) and len(friends)==2
                 and all(isinstance(row,dict) for row in friends)
                 and {row.get('bot_uuid') for row in friends}=={case._id('ENG'),case._id('PM')})
