"""Source binding for the complete original fixed-Loop parent Story."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
PARENT = 'story_fixed_loop_human_editor_workflow'
CASE_ID = 'LOCAL-FIXED-LOOP-01'
SUITE = 'owned-local-fixed-loop-v1'
REQUIREMENTS = tuple('FIXED_LOOP.' + name for name in (
    'sandbox-isolation', 'fixture-setup', 'approved-loop', 'exhausted-loop',
    'one-shot-publication', 'history-graph', 'exact-cleanup', 'reset-replacement'))
KINDS = ('fixture', 'http', 'http', 'http', 'http', 'poll', 'http', 'fixture')
METHODS = ('NONE', 'POST', 'POST', 'POST', 'GET', 'GET', 'DELETE', 'NONE')
ACTORS = ('LOCAL_CONTROL', 'HUMAN_LOCAL', 'HUMAN_LOCAL', 'HUMAN_LOCAL',
          'HUMAN_LOCAL', 'HUMAN_LOCAL', 'HUMAN_LOCAL', 'LOCAL_CONTROL')
SOURCES = {'fixed_loop_story.sh', 'fixed_loop_story.py', 'common.sh'}


def fixed_loop_method(text: str, name: str) -> str:
    """Return the one reviewed Python method; source drift is hash-checked."""
    tree = ast.parse(text, filename='fixed_loop_story.py')
    matches = [node for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and node.name == name]
    if len(matches) != 1:
        raise ValueError(f'fixed-Loop method {name} is missing or duplicated')
    node = matches[0]
    return '\n'.join(text.splitlines()[node.lineno - 1:node.end_lineno])


def _requirements_match(value: dict) -> bool:
    rows = value['requirements']
    return (
        tuple(row['id'] for row in rows) == REQUIREMENTS
        and tuple(row['kind'] for row in rows) == KINDS
        and tuple(row['method'] for row in rows) == METHODS
        and tuple(row['actor'] for row in rows) == ACTORS
    )


def fixed_loop_source_binding() -> dict:
    try:
        value = read(ROOT / 'fixed-loop-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent', 'sources', 'requirements'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != SOURCES or not _requirements_match(value)):
            raise ValueError

        texts: dict[str, str] = {}
        for name, digest in value['sources'].items():
            path = ROOT.parent / 'e2e-test' / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError
            texts[name] = raw.decode('utf-8')

        wrapper = texts['fixed_loop_story.sh']
        executor = texts['fixed_loop_story.py']
        if (wrapper.count(PARENT + '() {') != 1
                or 'fixed_loop_story.py' not in wrapper
                or 'E2E_TESTS_STORIES' not in wrapper):
            raise ValueError

        methods = {name: fixed_loop_method(executor, name)
                   for name in ('setup', 'start', 'exercise', 'verify', 'cleanup', 'main')}
        required_literals = {
            'setup': ('post("/control/reset", mock=True)', '"/me/ensure-human"', '"/providers"',
                      '/visibility', '"/friends/request"'),
            'start': ('"/control/provider/clear"', '"/control/judge/outcomes"', '"validate"',
                      '"/groups"', '/state-machine-runs', '/sessions'),
            'exercise': ('assert first["loop_context"]["iteration"] == 1',
                         'assert second["loop_context"]["iteration"] == 2',
                         'assert second["loop_context"]["previous_result"]["output"] == "first-editor-result"',
                         'assert second["loop_context"]["previous_result"]["outcome"] == "revise"',
                         'status=409', 'self.finish_bot'),
            'verify': ('len(outputs) == 6', 'assert len(published) == 1',
                       'assert session["status"] == "running"',
                       'assert self.cli("query", "--run", self.run_id, "--graph") == graph'),
            'cleanup': ('method="DELETE"', '"/control/reset"'),
            'main': ('story.setup()', 'story.exercise(True, Path(directory))',
                     'story.exercise(False, Path(directory))', 'story.cleanup()'),
        }
        for name, literals in required_literals.items():
            if any(literal not in methods[name] for literal in literals):
                raise ValueError(f'fixed-Loop original {name} behavior changed')

        expected_paths = ('${OWNED_FIXED_LOOP_SANDBOX}', '/me/ensure-human',
                          '/groups/${group_id}/state-machine-runs',
                          '/groups/${group_id}/sessions', '/sessions/${session_id}',
                          '/state-machine-runs/${run_id}/graph',
                          '/groups/${group_id}?bot_id=${driver_bot_id}',
                          '/control/judge/outcomes')
        if tuple(row['path'] for row in value['requirements']) != expected_paths:
            raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id', 'kind', 'method', 'path', 'actor', 'expectation'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 2048):
                raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('fixed-Loop original source map changed') from None
