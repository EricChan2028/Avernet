"""Source binding for the human delivery-policy CAS parent Story.

The original shell story drives ten ordered HTTP calls against the
effective delivery policy: human reads, Bot read/write denial, a
human CAS update with optimistic versioning, a stale-write conflict,
and a full restoration. This module binds each obligation to its
replayed method and path; drift in the parent function or the frozen
map requires an explicit migration review, never silent regeneration.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = 'LOCAL-DELIVERY-POLICY-01'
SUITE = 'owned-local-delivery-policy-v1'
PARENT = 'story_human_manages_delivery_policy'
PATH = '/admin/message-delivery/policy'
REQUIREMENTS = tuple('DELIVERY.'+x for x in (
    'human-read','bot-read-denied','bot-write-denied','denied-preserves',
    'cas-update','readback','stale-rejected','conflict-preserves','restore','restored'))
# Replayed actor per obligation index: indices 1 and 2 are the Bot
# denial probes; every other call is the authenticated human.
KINDS = ('api','bot','bot','api','api','api','api','api','api','api')


def delivery_policy_source_binding():
    try:
        value = read(ROOT/'delivery-policy-migration-contract.json')
        if (set(value) != {'schema_version','source_revision','parent','sources','requirements'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'stories.sh'}):
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
        start = text.index(PARENT+'() {')
        end = text.index('\n}', start)
        body = text[start:end]
        if 'local path='+json_quote(PATH) not in body:
            raise ValueError
        actual = re.findall(r'^    (api|bot)_(get|put|post|patch|delete) "\$path"', body, re.M)
        expected = [(KINDS[i], REQUIREMENTS[i].split('.',1)[1].rsplit('-',1)[0]
                     if False else None) for i in range(len(REQUIREMENTS))]
        if [kind for kind,_ in actual] != list(KINDS):
            raise ValueError
        methods = [row['method'].lower() for row in value['requirements']]
        if [(kind, method) for kind, method in actual] != list(zip(KINDS, methods)):
            raise ValueError
        if [row['id'] for row in value['requirements']] != list(REQUIREMENTS):
            raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id','method','path','actor','kind','expectation'}
                    or row['method'] not in ('GET','PUT') or row['path'] != PATH
                    or row['actor'] != ('bot' if row['id'] in (
                        'DELIVERY.bot-read-denied','DELIVERY.bot-write-denied') else 'human')
                    or row['kind'] != 'http'
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 1024):
                raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError('Delivery policy source or complete ten-call map changed') from None


def json_quote(path: str) -> str:
    return '"'+path+'"'
