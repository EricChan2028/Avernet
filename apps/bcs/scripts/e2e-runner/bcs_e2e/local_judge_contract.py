"""Full original slow-Judge obligations, including the one-second callback."""
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError,read

ROOT=Path(__file__).resolve().parents[1]
PARENT='story_provider_callback_survives_slow_judge'
CASE_ID='LOCAL-SLOW-JUDGE-01'
SUITE='owned-local-slow-judge-v1'
REQUIREMENTS=tuple('JUDGE.'+name for name in (
    'sandbox-empty','provider-register','bot-publish','bot-public','friend-request','friends-read',
    'group-create','explicit-receipt-boundary','run-start','review-delivery','awaiting-response',
    'publish-receipt-boundary','review-callback-deadline','judge-started','judging','review-node',
    'judge-release','publish-delivery','publish-callback','run-completed','group-delete','bot-retire'))
KINDS=('fixture','http','http','http','http','http','http','fixture','http','delivery','poll',
       'fixture','http','observation','poll','http','fixture','delivery','http','poll','http','http')
_HEADERS=re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\(\) \{$',re.M)


def source_body(text,name,next_name):
    rows=list(_HEADERS.finditer(text));matches=[i for i,row in enumerate(rows) if row.group()==name+'() {']
    if len(matches)!=1:raise ValueError
    i=matches[0]
    if i+1>=len(rows) or rows[i+1].group()!=next_name+'() {':raise ValueError
    chunk=text[rows[i].start():rows[i+1].start()];ends=list(re.finditer(r'^}\s*$',chunk,re.M))
    if not ends:raise ValueError
    return chunk[:ends[-1].end()]


def judge_source_binding():
    try:
        value=read(ROOT/'judge-migration-contract.json')
        if (set(value)!={'schema_version','source_revision','parent','sources','requirements'}
                or value['schema_version']!=1 or value['parent']!=PARENT
                or not re.fullmatch('[0-9a-f]{40}',value['source_revision'])
                or set(value['sources'])!={'stories.sh','common.sh','http_provider_judge_mock.py','mock_services.sh'}
                or [r['id'] for r in value['requirements']]!=list(REQUIREMENTS)
                or tuple(r['kind'] for r in value['requirements'])!=KINDS):raise ValueError
        texts={}
        for name,digest in value['sources'].items():
            path=ROOT.parent/'e2e-test'/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size>1_048_576:raise ValueError
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError
            texts[name]=raw.decode('utf-8')
        body=source_body(texts['stories.sh'],PARENT,'story_provider_operator_publishes_agent')
        flat=' '.join(body.replace('\\\n',' ').split())
        http=[r for r in value['requirements'] if r['kind']=='http' and r['id']!=REQUIREMENTS[12]]
        operations=re.findall(r'^\s+(?:api_request_headers\s+(GET|POST|PUT|PATCH|DELETE)|api_(get|post|put|patch|delete))\s+"([^"\n]+)"',body.replace('\\\n',' '),re.M)
        if [(a or b).upper() for a,b,_ in operations]!=[r['method'] for r in http]:raise ValueError
        if [path for _,_,path in operations]!=[r['path'] for r in http]:raise ValueError
        for index,row in enumerate(value['requirements']):
            if (set(row)!={'id','kind','method','path','actor','expectation'} or not isinstance(row['expectation'],str)
                    or not 1<=len(row['expectation'])<=2048):raise ValueError
            expected_actor=('PROVIDER_ADMIN' if index in (2,21) else 'PROVIDER_RUNTIME' if index in (3,12,18)
                            else 'BCS_PROVIDER' if row['kind']=='delivery' else 'HUMAN_LOCAL' if row['kind'] in ('http','poll') else 'LOCAL_CONTROL')
            if row['actor']!=expected_actor:raise ValueError
        for literal in ('provider_callback_with_timeout "$provider_id" "$runtime_token"',
            'wait_for_graph_node_sub_status "$run_id" "review" "awaiting_response"',
            'wait_for_graph_node_sub_status "$run_id" "review" "judging"',
            'wait_for_state_machine_status "$run_id" "completed"',
            'candidate awaiting slow judge','published after slow judge','judge_outputs.0.decision.outcome',
            'api_request_headers PUT "/bots/${provider_bot_uuid}/visibility"', '"X-BCS-Bot-Token: ${runtime_token}',
            '"running"','"Bearer ${bcs_token}"','/control/judge/release'):
            if literal not in flat:raise ValueError
        for index,method,path in ((10,'GET','/state-machine-runs/${run_id}/graph'),
                                  (12,'POST','/bot/events'),
                                  (14,'GET','/state-machine-runs/${run_id}/graph'),
                                  (19,'GET','/state-machine-runs/${run_id}')):
            row=value['requirements'][index]
            if row['method']!=method or row['path']!=path:raise ValueError
        callback=source_body(texts['stories.sh'],'provider_callback_with_timeout','story_user_prepares_agent_network')
        if '--max-time 1 -X POST' not in callback or '${BCS_API_BASE_URL}/bot/events' not in callback:raise ValueError
        if 'STATE.judge_release.wait(timeout=30)' not in texts['http_provider_judge_mock.py']:raise ValueError
        if len(re.findall('/control/provider/clear',body))!=2:raise ValueError
        return value
    except (OSError,ValueError,TypeError,KeyError):
        raise RunStateError('Slow Judge original source map or callback deadline changed') from None
