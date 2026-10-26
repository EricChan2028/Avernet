"""Full original six-step disabled-channel HTTP Story; CLI is a separate parent."""
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = 'LOCAL-CHANNEL-HTTP-01'
SUITE = 'owned-local-channel-http-v1'
PARENT = 'story_user_validates_external_channel_setup'
REQUIREMENTS = tuple('CHANNEL.'+x for x in ('bind-denied','list-before','target-empty','pause-missing','delete-missing','list-after'))


def channel_source_binding():
    try:
        value = read(ROOT/'channel-http-migration-contract.json')
        if (set(value) != {'schema_version','source_revision','parent','sources','requirements'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'stories.sh'}
                or [r['id'] for r in value['requirements']] != list(REQUIREMENTS)):
            raise ValueError
        path = ROOT.parent/'e2e-test/stories.sh'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value['sources']['stories.sh']:
            raise ValueError
        text = raw.decode('utf-8')
        if len(re.findall(r'^'+PARENT+r'\(\) \{$', text, re.M)) != 1:
            raise ValueError
        start = text.index(PARENT+'() {'); end = text.index('\n}', start)
        actual = [(method.upper(), path) for method,path in re.findall(
            r'^    api_(post|get|patch|delete) "([^"]+)"', text[start:end], re.M)]
        if actual != [(r['method'],r['path']) for r in value['requirements']]:
            raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id','method','path','expectation'} or not isinstance(row['expectation'],str)
                    or not 1 <= len(row['expectation']) <= 1024):
                raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError('Channel HTTP source or complete six-step map changed') from None
