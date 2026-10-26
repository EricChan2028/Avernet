"""Complete direct-chat parent including its hidden preliminary ping Run."""
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT=Path(__file__).resolve().parents[1]
CASE_ID='LOCAL-DIRECT-CHAT-01'
SUITE='owned-local-direct-chat-v1'
PARENT='story_user_has_direct_agent_conversation'
REQUIREMENTS=tuple('DM.'+x for x in ('ping-submit','main-submit','main-read','main-cancel','main-terminal'))


def direct_chat_source_binding():
    try:
        value=read(ROOT/'direct-chat-migration-contract.json')
        if (set(value)!={'schema_version','source_revision','parent','sources','requirements'}
                or value['schema_version']!=1 or value['parent']!=PARENT
                or not re.fullmatch('[0-9a-f]{40}',value['source_revision'])
                or set(value['sources'])!={'stories.sh','actor.sh','common.sh'}
                or [r['id'] for r in value['requirements']]!=list(REQUIREMENTS)):
            raise ValueError
        texts={}
        for name,digest in value['sources'].items():
            path=ROOT.parent/'e2e-test'/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size>1_048_576: raise ValueError
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError
            texts[name]=raw.decode()
        def body(name,source):
            text=texts[source]
            if len(re.findall(r'^'+name+r'\(\) \{$',text,re.M))!=1:raise ValueError
            start=text.index(name+'() {');return text[start:text.index('\n}',start)+2]
        parent=body(PARENT,'stories.sh');helper=body('test_bot_chat_async','actor.sh')
        if parent.count('    test_bot_chat_async\n')!=1:raise ValueError
        actual=[('POST',p) for p in re.findall(r'_bot_request POST "([^"]+)"',helper)]
        actual.extend((m.upper(),p) for m,p in re.findall(r'^    bot_(post|get) "([^"]+)" CEO',parent,re.M))
        if actual!=[(r['method'],r['path']) for r in value['requirements']]:raise ValueError
        for row in value['requirements']:
            if (set(row)!={'id','method','path','expectation'} or not isinstance(row['expectation'],str)
                    or not 1<=len(row['expectation'])<=1024):raise ValueError
        return value
    except (ValueError,KeyError,TypeError,OSError):
        raise RunStateError('Direct-chat original complete source mapping changed') from None
