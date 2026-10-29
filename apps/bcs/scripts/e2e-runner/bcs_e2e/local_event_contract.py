"""Complete original Event Webhook obligations, including callback observations."""
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT=Path(__file__).resolve().parents[1]
PARENT='story_user_receives_group_event_webhooks'
CASE_ID='LOCAL-EVENT-WEBHOOK-01'
SUITE='owned-local-event-webhook-v1'
REQUIREMENTS=tuple('WEBHOOK.'+name for name in (
    'receiver-empty','local-principal','group-create','group-event','subscription-list',
    'subscription-read','subscription-test','test-event','subscription-disable','subscription-enable',
    'session-create','session-event','message-create','message-event','stream-order','deliveries-list',
    'delivery-read','subscription-delete','session-delete','group-delete'))
KINDS=('fixture','identity','http','delivery','http','http','http','delivery','http','http',
       'http','delivery','http','delivery','observation','http','http','http','http','http')
HEADERS=re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\(\) \{$',re.M)


def source_body(text, name, next_name):
    headers=list(HEADERS.finditer(text))
    positions=[i for i,row in enumerate(headers) if row.group()==name+'() {']
    if len(positions)!=1:raise ValueError('Event Story source header differs')
    i=positions[0]
    if i+1>=len(headers) or headers[i+1].group()!=next_name+'() {':raise ValueError('Event Story boundary differs')
    chunk=text[headers[i].start():headers[i+1].start()]
    endings=list(re.finditer(r'^}\s*$',chunk,re.M))
    if not endings:raise ValueError('Event Story terminator missing')
    return chunk[:endings[-1].end()]


def event_source_binding():
    try:
        value=read(ROOT/'webhook-migration-contract.json')
        if (set(value)!={'schema_version','source_revision','parent','sources','requirements'}
                or value['schema_version']!=1 or value['parent']!=PARENT
                or not re.fullmatch('[0-9a-f]{40}',value['source_revision'])
                or set(value['sources'])!={'stories.sh','common.sh','http_provider_judge_mock.py','group_session_ws_probe.py'}
                or [r['id'] for r in value['requirements']]!=list(REQUIREMENTS)
                or tuple(r['kind'] for r in value['requirements'])!=KINDS):raise ValueError
        texts={}
        for name,digest in value['sources'].items():
            path=ROOT.parent/'e2e-test'/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size>1_048_576:raise ValueError
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError
            texts[name]=raw.decode('utf-8')
        body=source_body(texts['stories.sh'],PARENT,'story_provider_callback_survives_slow_judge')
        normalized=' '.join(body.replace('\\\n',' ').split())
        http=[row for row in value['requirements'] if row['kind']=='http']
        matches=re.findall(r'^\s+(?:api_request_headers\s+(GET|POST|PATCH|DELETE)|api_(get|post|patch|delete))\b',body,re.M)
        actual=[(a or b).upper() for a,b in matches]
        if len(http)!=13 or actual!=[row['method'] for row in http]:raise ValueError
        for row in value['requirements']:
            if (set(row)!={'id','kind','method','path','actor','expectation'}
                    or not isinstance(row['expectation'],str) or not 1<=len(row['expectation'])<=2048):raise ValueError
            if row['kind']=='http':
                public=row['path'].startswith('${public_prefix}')
                command=('api_request_headers '+row['method'] if public else 'api_'+row['method'].lower())
                if command+' "'+row['path']+'"' not in normalized:raise ValueError
                if row['actor']!=('HUMAN_PRINCIPAL' if public else 'HUMAN_LOCAL'):raise ValueError
        events=re.findall(r'wait_for_event_webhook_type "([a-z_.]+)" "\$group_id"',body)
        if events!=['group.created','event_subscription.test','session.created','message.created']:raise ValueError
        required_literals=['/control/event-webhook/clear','/control/event-webhook/requests',
            'values == sorted(values)','len(values) == len(set(values))','"authorization" "null"',
            'public_prefix="/openapi/v1/collaboration"','--signing-key "$signing_key"']
        if any(literal not in normalized for literal in required_literals):raise ValueError
        helper=source_body(texts['stories.sh'],'wait_for_event_webhook_type','wait_for_mock_judge_start')
        if 'seq 1 200' not in helper or 'scope' not in helper or 'group_id' not in helper:raise ValueError
        return value
    except (OSError,ValueError,TypeError,KeyError):
        raise RunStateError('Event Webhook complete source map changed') from None
