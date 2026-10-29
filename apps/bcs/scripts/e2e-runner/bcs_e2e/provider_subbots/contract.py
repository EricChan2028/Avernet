"""Source-bound contract for the Provider subbots owned-local slice.

The upstream story is one self-checking Python child over a live BCS HTTP
surface.  It registers one `agentpass-subbots` Provider, creates sibling Bots
through both registration paths, and proves Actor/credential/parent/ref
isolation, callback identity, signed-principal persistence, disable/enable and
deletion tombstones.  This module freezes that source and the finite obligation
map before any owned runtime starts.

Preprod is deliberately unsupported: the public-v1 profile has no Provider
control-plane bindings and no provider_admin_token credential channel.  This
slice grants no release credit and remains an independent backlog migration.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

from ..inventory import KNOWN_UNMAPPED_LEGACY_IDS
from ..run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[2]
PARENT = 'story_provider_operator_manages_subbots'
CASE_ID = 'LOCAL-PROVIDER-SUBBOTS-01'
SUITE = 'owned-local-provider-subbots-v1'
STATUS = 'LOCAL_PROVIDER_SUBBOTS_PASS'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'agentpass_subbots_story.py'
SHELL_SOURCE = ROOT.parent / 'e2e-test' / 'e2e.sh'
PRODUCT_DOC = ROOT.parents[1] / 'docs' / 'agentpass-subbots.md'
REPO_ROOT = ROOT.parents[3]
STORY_RELATIVE = 'apps/bcs/scripts/e2e-test/agentpass_subbots_story.py'
SHELL_RELATIVE = 'apps/bcs/scripts/e2e-test/e2e.sh'
DOC_RELATIVE = 'apps/bcs/docs/agentpass-subbots.md'
CONFIG_FRAGMENT = Path(__file__).with_name('provider-subbots-required.json')
SOURCE_REVISION = '49ce1afc9f3d2b1f1dc9ec485f8cd368ade2b873'
REQUIREMENTS = tuple('PROVIDER-SUBBOTS.' + name for name in (
    'provider-registration-discovery',
    'admin-complete-ref-validation',
    'admin-sibling-registration',
    'scoped-complete-ref-validation',
    'scoped-sibling-distinct-identity',
    'duplicate-reference-conflicts',
    'self-lookup-actor-binding',
    'legacy-api-actor-binding',
    'metadata-update-parent-immutability',
    'onboard-parent-immutability',
    'parent-discovery-fail-closed',
    'callback-actor-binding',
    'signed-principal-live-state',
    'disable-enable-state',
    'deletion-tombstone-retirement',
))
MARKER_KEYS = ('owned-local-provider-subbots-compliance',)
MARKER_PATTERNS = (
    re.compile(r'^Provider subbots story: ([0-9]+) live HTTP assertions passed$', re.M),
)
STORY_MARKERS = ('Provider subbots story: {checks} live HTTP assertions passed',)
MARKER_PREFIX = 'Provider subbots story: '
MARKER_SUFFIX = ' live HTTP assertions passed'
# Story.checks accounting: 47 direct request status checks outside self_view;
# 15 self_view executions contribute 75 checks (9 successful x 7 and 6
# rejected x 2); and 9 explicit checks outside self_view: 131 total.
STORY_CHECKS = 131
CONFIG_REQUIRED = {
    'provider_stream_gray_enabled': False,
    'provider_stream_gray_created_by': [],
}
REDACTION_TOKENS = (
    'bot' + '_token', 'bot_runtime' + '_token', 'provider_admin' + '_token',
    'X-Avernet-' + 'Principal', 'Bear' + 'er ', 'coo' + 'kie',
)
ENVIRONMENT = {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'}
DOCUMENTED_GAPS = ('PREPROD_PROVIDER_CONTROL_PLANE_ABSENT',)
DRIFT_ERROR = 'provider-subbots upstream source drift requires review'

ANCHORS = {
    'PROVIDER-SUBBOTS.provider-registration-discovery': (
        '"create subbots Provider"',
        '"auth": {"mode": mode}',
        'public["auth_mode"] == mode',
    ),
    'PROVIDER-SUBBOTS.admin-complete-ref-validation': (
        '"provider_bot_ref": f"{parent}:reviewer"',
        'for invalid in ("parent-without-slug", ":a", f"{parent}:", f"{parent}:Uppercase",',
        'token=admin,',
    ),
    'PROVIDER-SUBBOTS.admin-sibling-registration': (
        '"register reviewer with Provider credential"',
        'first["agent_code"] == parent and first["bot_slug"] == "reviewer"',
        '"reject duplicate reviewer"',
    ),
    'PROVIDER-SUBBOTS.scoped-complete-ref-validation': (
        '"owner issues scoped registration credential"',
        '"removed bot_slug cannot complete a malformed ref"',
        '"scoped registration rejects malformed complete ref"',
    ),
    'PROVIDER-SUBBOTS.scoped-sibling-distinct-identity': (
        '"register coder with scoped credential"',
        'identity["agent_code"] == parent and identity["bot_slug"] == "coder"',
        'a["id"] != b["id"] and a["token"] != b["token"]',
    ),
    'PROVIDER-SUBBOTS.duplicate-reference-conflicts': (
        '"reject duplicate reviewer"',
        '"scoped duplicate fails at registration"',
        '409,',
    ),
    'PROVIDER-SUBBOTS.self-lookup-actor-binding': (
        'self.self_view(bot, parent)',
        'self.self_view(bot, parent, actor=sibling["id"], status=401)',
        '"Actor alone cannot fall back to human"',
        '"malformed Actor is rejected"',
    ),
    'PROVIDER-SUBBOTS.legacy-api-actor-binding': (
        '"legacy auth accepts matching Actor"',
        '"legacy auth rejects sibling Actor despite mock user"',
    ),
    'PROVIDER-SUBBOTS.metadata-update-parent-immutability': (
        '"update reviewer metadata"',
        'changed["bot_uuid"] == a["id"] and changed["name"] == "E2E Senior Reviewer"',
    ),
    'PROVIDER-SUBBOTS.onboard-parent-immutability': (
        '"onboarding metadata preserves parent identity"',
        'self.self_view(a, parent)',
    ),
    'PROVIDER-SUBBOTS.parent-discovery-fail-closed': (
        '"parent discovery requires credentials"',
        'for query_string in ("limit=0", "limit=101", "cursor=", "unknown=value")',
        '"public build fails closed without AgentPass verifier"',
        '"agent_identity_unavailable"',
    ),
    'PROVIDER-SUBBOTS.callback-actor-binding': (
        '"authenticated callback reaches run lookup"',
        'result["error"] == "run_not_found"',
        '"callback rejects sibling Actor"',
        '"callback accepts Bot token without Actor"',
    ),
    'PROVIDER-SUBBOTS.signed-principal-live-state': (
        '"X-Avernet-Principal": self.signed_bot(a, parent)',
        '"Gateway principal uses signed Bot"',
        '"X-BCN-Actor-Bot-Id": b["id"]',
    ),
    'PROVIDER-SUBBOTS.disable-enable-state': (
        '"disable Provider"',
        'self.self_view(a, parent, status=401)',
        '"disabled Provider invalidates signed Bot"',
        '"enable Provider"',
        '"enabled Provider restores signed Bot"',
    ),
    'PROVIDER-SUBBOTS.deletion-tombstone-retirement': (
        '"delete reviewer"',
        '"deleted slug cannot be reclaimed"',
        '"scoped path cannot reclaim deleted slug"',
        '"delete coder"',
        '"retire test Provider"',
    ),
}


def _git_tip(relative: str) -> str:
    try:
        result = subprocess.run(
            ['git', '-C', str(REPO_ROOT), 'log', '-1', '--format=%H', '--', relative],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        raise RunStateError('provider-subbots git tip revision is unavailable') from None
    value = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch('[0-9a-f]{40}', value) is None:
        raise RunStateError('provider-subbots git tip revision is not a full commit digest')
    return value


def provider_subbots_adaptability() -> dict:
    """Declare the dual-environment row without release credit."""
    return {
        'case_id': CASE_ID,
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'selected_obligations': [name.split('.', 1)[1] for name in REQUIREMENTS],
        'public_paths': [],
        'environments': ['owned-local-full-story'],
        'local_executor_status': 'LOCAL_EXECUTOR_ONLY',
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': (
            'The preprod public-v1 route bindings expose only group.* and session.* operations; '
            'they declare no Provider registration/admin surface, parent-discovery or Provider '
            'callback binding, and no provider_admin_token credential channel '
            '(PREPROD_PROVIDER_CONTROL_PLANE_ABSENT). Remote validation is therefore NOT_RUN '
            'and this independent backlog slice grants no release credit.'),
    }


def _line_text(path: Path, line_number: int) -> str:
    lines = path.read_text(encoding='utf-8').splitlines()
    if line_number < 1 or line_number > len(lines):
        raise ValueError('anchor line is outside the upstream source')
    return lines[line_number - 1]


def _validate_anchors(rows: list, source: Path) -> None:
    if [row['id'] for row in rows] != list(REQUIREMENTS):
        raise ValueError('requirement order differs from the frozen map')
    for row in rows:
        if set(row) != {'id', 'expectation', 'story_lines', 'anchors', 'markers'}:
            raise ValueError('requirement shape changed')
        if not isinstance(row['expectation'], str) or not 1 <= len(row['expectation']) <= 1024:
            raise ValueError('requirement expectation is invalid')
        lines = row['story_lines']
        if (not isinstance(lines, list) or len(lines) != 2
                or any(type(item) is not int for item in lines)
                or not 1 <= lines[0] <= lines[1]
                or lines[1] > len(source.read_text(encoding='utf-8').splitlines())):
            raise ValueError('requirement story_lines are invalid')
        if not isinstance(row['anchors'], list) or not row['anchors']:
            raise ValueError('requirement anchors are missing')
        for anchor in row['anchors']:
            if set(anchor) != {'line', 'literal'} or type(anchor['line']) is not int:
                raise ValueError('anchor shape changed')
            if anchor['line'] < lines[0] or anchor['line'] > lines[1]:
                raise ValueError('anchor line is outside its requirement range')
            if anchor['literal'] not in _line_text(source, anchor['line']):
                raise RunStateError('provider-subbots obligation anchor changed: ' + row['id'])
        if row['markers'] != list(MARKER_KEYS):
            raise ValueError('requirement marker changed')
        for literal in ANCHORS[row['id']]:
            if literal not in source.read_text(encoding='utf-8'):
                raise RunStateError('provider-subbots obligation literal changed: ' + row['id'])


def provider_subbots_source_binding() -> dict:
    """Freeze and validate the upstream story, shell entry, doc and obligations."""
    try:
        value = read(ROOT / 'provider-subbots-migration-contract.json')
        source_paths = {
            'agentpass_subbots_story.py': STORY_SOURCE,
            'e2e.sh': SHELL_SOURCE,
            'agentpass-subbots.md': PRODUCT_DOC,
        }
        if (set(value) != {'schema_version', 'parent', 'suite', 'case_id', 'environment',
                           'sources', 'source_revision', 'source_file_revisions',
                           'story_checks', 'story_markers', 'owned_runtime_binary',
                           'config_fragment', 'redaction_tokens', 'requirements',
                           'documented_gaps', 'adaptability', 'remote_validation',
                           'grants_release_credit', 'release_parent_denominator',
                           'independent_slice', 'evidence_granularity'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['suite'] != SUITE or value['case_id'] != CASE_ID
                or value['environment'] != ENVIRONMENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or value['source_revision'] != SOURCE_REVISION
                or set(value['source_file_revisions']) != set(source_paths)
                or any(not re.fullmatch('[0-9a-f]{40}', digest)
                       for digest in value['source_file_revisions'].values())
                or value['source_revision'] != _git_tip(STORY_RELATIVE)
                or value['source_file_revisions']['agentpass_subbots_story.py'] != _git_tip(STORY_RELATIVE)
                or value['source_file_revisions']['e2e.sh'] != _git_tip(SHELL_RELATIVE)
                or value['source_file_revisions']['agentpass-subbots.md'] != _git_tip(DOC_RELATIVE)
                or set(value['sources']) != set(source_paths)
                or type(value['story_checks']) is not int or value['story_checks'] != STORY_CHECKS
                or value['story_markers'] != list(STORY_MARKERS)
                or value['redaction_tokens'] != list(REDACTION_TOKENS)
                or value['remote_validation'] != 'NOT_RUN'
                or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not False
                or value['independent_slice'] is not True
                or value['evidence_granularity'] != 'OWNED_CHILD_RECEIPT'
                or value['adaptability'] != provider_subbots_adaptability()
                or set(value['documented_gaps']) != set(DOCUMENTED_GAPS)):
            raise ValueError
        runtime = value['owned_runtime_binary']
        if (set(runtime) != {'package', 'build_command', 'hash_algorithm', 'opt_in_env',
                             'auto_build', 'required_arguments'}
                or runtime['package'] != 'bcs'
                or runtime['build_command'] != 'cargo build -p bcs --bin bcs'
                or runtime['hash_algorithm'] != 'sha256'
                or runtime['opt_in_env'] != ['BCS_E2E_TEST_BINARY',
                                             'BCS_E2E_TEST_BINARY_SHA256']
                or runtime['auto_build'] is not False
                or runtime['required_arguments'] != ['--binary', '--binary-sha256']):
            raise ValueError
        for name, digest in value['sources'].items():
            path = source_paths[name]
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RunStateError(DRIFT_ERROR)
        story_text = STORY_SOURCE.read_text(encoding='utf-8')
        if not story_text.startswith('#!/usr/bin/env python3'):
            raise ValueError
        if 'PASS: disable/re-enable and deletion enforce persisted identity' not in story_text:
            raise RunStateError('provider-subbots upstream completion marker changed')
        gap = value['documented_gaps']['PREPROD_PROVIDER_CONTROL_PLANE_ABSENT']
        if (not isinstance(gap, str) or not 1 <= len(gap) <= 2048
                or 'provider_admin_token' not in gap or 'preprod' not in gap.lower()):
            raise ValueError
        config = value['config_fragment']
        if (set(config) != {'path', 'required', 'integration_note'}
                or config['path'] != 'bcs_e2e/provider_subbots/provider-subbots-required.json'
                or config['required'] != CONFIG_REQUIRED
                or not isinstance(config['integration_note'], str)
                or 'provider_stream_gray_enabled=false' not in config['integration_note']):
            raise ValueError
        fragment = CONFIG_FRAGMENT
        if fragment.is_symlink() or not fragment.is_file():
            raise ValueError
        try:
            fragment_value = json.loads(fragment.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise RunStateError('provider-subbots config fragment changed') from None
        if fragment_value != CONFIG_REQUIRED:
            raise RunStateError('provider-subbots config fragment changed')
        _validate_anchors(value['requirements'], STORY_SOURCE)
        if PARENT not in KNOWN_UNMAPPED_LEGACY_IDS:
            raise RunStateError('provider-subbots parent left the known-unmapped backlog')
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('provider-subbots source binding changed') from None
