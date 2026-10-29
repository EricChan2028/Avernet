"""Whole original CLI Channel parent, including fixture lifecycle and retirement."""
import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = 'LOCAL-CHANNEL-CLI-01'
SUITE = 'owned-local-channel-cli-v1'
PARENT = 'story_cli_operator_validates_channel_management'
REQUIREMENTS = tuple('CHANNEL-CLI.'+x for x in ('bind-redacted','list-before','group-create',
    'session-create','conversation-empty','unbind-missing','list-after','session-delete','group-delete'))
COMMANDS = ('channel bind','channel list','create-group','session create','channel conversation-id',
            'channel unbind','channel list','DELETE session','DELETE group')


def channel_cli_source_binding():
    try:
        value = read(ROOT/'channel-cli-migration-contract.json')
        if (set(value) != {'schema_version','source_revision','parent','sources','requirements'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or not re.fullmatch('[0-9a-f]{40}',value['source_revision'])
                or set(value['sources']) != {'cli-stories.sh','common.sh','stories.sh'}
                or [r['id'] for r in value['requirements']] != list(REQUIREMENTS)
                or [r['command'] for r in value['requirements']] != list(COMMANDS)):
            raise ValueError
        texts = {}
        for name,digest in value['sources'].items():
            path = ROOT.parent/'e2e-test'/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError
            texts[name] = raw.decode()
        def body(name):
            text = texts['cli-stories.sh']
            if len(re.findall(r'^'+name+r'\(\) \{$',text,re.M)) != 1:
                raise ValueError
            start = text.index(name+'() {')
            return text[start:text.index('\n}',start)]
        source = body(PARENT)
        anchors = ('if bcs_cli_json CEO channel bind',
            '_cli_story_run "operator lists channel bindings" CEO channel list',
            '_cli_story_create_pm_group "CLI channel conversation lookup"',
            '_cli_story_run "operator creates an authorized channel lookup session" PM session create',
            '_cli_story_run "participating bot resolves DingTalk conversations for a session" PM channel conversation-id',
            '_cli_story_run "operator unbinds a missing disabled-bridge binding" CEO channel unbind',
            '_cli_story_run "operator confirms no phantom channel binding exists" CEO channel list',
            'api_delete "/sessions/${session_id}?bot_id=${BOT_PM_UUID}"',
            'api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"')
        if any(source.count(anchor) != 1 for anchor in anchors):
            raise ValueError
        positions = [source.index(anchor) for anchor in anchors]
        if positions != sorted(positions) or 'PM create-group' not in body('_cli_story_create_pm_group'):
            raise ValueError
        if '--context "Release coordination through bcs-cli"' not in body('_cli_story_create_pm_group'):
            raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id','command','expectation'} or not isinstance(row['expectation'],str)
                    or not 1 <= len(row['expectation']) <= 2048):
                raise ValueError
        return value
    except (ValueError, OSError, TypeError, KeyError):
        raise RunStateError('CLI Channel complete source or obligation map changed') from None
