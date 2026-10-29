"""Original structured-collaboration parent, including embedded Python bodies."""
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT=Path(__file__).resolve().parents[1]
CASE_ID='LOCAL-STRUCTURED-01'
SUITE='owned-local-structured-v1'
PARENT='story_user_runs_structured_collaboration'
REQUIREMENTS=tuple('STRUCTURED.'+name for name in (
    'templates-list','template-detail','group-create','group-read','definition-read','definition-patch',
    'upgrade-missing','run-start','run-read','graph-read','node-read','pending-human-empty',
    'human-response-denied','run-cancel','run-terminal','rerun-aborted-denied','group-delete'))
_FUNCTION=re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\(\) \{$',re.M)


def original_body(text):
    """Finite reviewed source slice, not a general shell parser.

    The first newline+brace belongs to embedded Python json.dumps, not the shell
    function. Whole-file hashes and the exact next top-level shell header bound
    this specific Story; taking the final isolated brace keeps all17 operations.
    """
    matches=[m for m in _FUNCTION.finditer(text) if m.group()==PARENT+'() {']
    if len(matches)!=1: raise ValueError('structured parent header missing or duplicate')
    start=matches[0].start()
    following=next((m for m in _FUNCTION.finditer(text) if m.start()>start),None)
    if following is None or following.group()!='story_user_receives_group_event_webhooks() {':
        raise ValueError('structured parent boundary changed')
    chunk=text[start:following.start()]
    endings=list(re.finditer(r'^}\s*$',chunk,re.M))
    if not endings: raise ValueError('structured parent terminator missing')
    return chunk[:endings[-1].end()]


def structured_source_binding():
    try:
        value=read(ROOT/'structured-migration-contract.json')
        if (set(value)!={'schema_version','source_revision','parent','sources','requirements'}
                or value['schema_version']!=1 or value['parent']!=PARENT
                or not re.fullmatch('[0-9a-f]{40}',value['source_revision'])
                or set(value['sources'])!={'stories.sh','common.sh'}
                or [r['id'] for r in value['requirements']]!=list(REQUIREMENTS)):
            raise ValueError
        texts={}
        for name,digest in value['sources'].items():
            path=ROOT.parent/'e2e-test'/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size>1_048_576:raise ValueError
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError
            texts[name]=raw.decode('utf-8')
        body=original_body(texts['stories.sh'])
        matches=re.findall(r'^    (api|bot)_(get|post|patch|delete) "([^"]+)"(?: (CEO))?',body,re.M)
        actual=[(method.upper(),path,'CEO' if wrapper=='bot' and role=='CEO' else 'HUMAN')
                for wrapper,method,path,role in matches]
        if len(actual)!=17 or actual!=[(r['method'],r['path'],r['actor']) for r in value['requirements']]:raise ValueError
        for row in value['requirements']:
            if (set(row)!={'id','method','path','actor','expectation'} or not isinstance(row['expectation'],str)
                    or not 1<=len(row['expectation'])<=2048):raise ValueError
        return value
    except (OSError,ValueError,TypeError,KeyError):
        raise RunStateError('Structured collaboration original17-step source map changed') from None
