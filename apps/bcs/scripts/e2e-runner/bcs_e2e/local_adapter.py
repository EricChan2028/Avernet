"""Composition of shared pilot operations with a live owned local BCS.

No saved PID, arbitrary origin, developer cookie or remote signing material can
activate this adapter. The process-owning composition root supplies the handle.
"""
from __future__ import annotations

import json
from pathlib import Path

from .adapters import RemotePilotAdapter
from .file_transport import RemoteFileClient
from .local_auth import OwnedLocalAuth
from .local_fixtures import OwnedLocalFixtures
from .local_sqlite import OwnedSqliteEvidenceProvider
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, validate
from .run_state import read, require_profile_binding
from .transport import ReadClient
from .write_transport import WriteClient
from .ws_transport import SessionWebSocketClient


def resolve_owned_profile(runtime: OwnedBcsProcess) -> dict:
    """Resolve generated actors before profile fingerprint/Run/lease creation."""
    fixtures = OwnedLocalFixtures(runtime).check()
    template = Path(__file__).resolve().parents[1] / 'profiles/local.singlebox.example.json'
    profile = json.loads(template.read_text())
    # Distinct run-owned runtimes never borrow a shared operational fixture pool.
    scope = 'owned-' + fingerprint({'root': str(runtime.root), 'run_id': runtime.run_id})[:24]
    profile['environment'].update(id=scope, expected_bcs_env='local')
    profile['entrypoint']['origin'] = runtime.inputs.origin
    profile['human'] = {'user_id': runtime.inputs.human_id}
    profile['actors'] = {role: {key: value[key] for key in ('bot_id', 'owner_id', 'dedicated')}
                         for role, value in fixtures['roles'].items()}
    profile['auth'] = {'kind': 'owned-local'}
    profile['observer'] = {'kind': 'local-sqlite', 'server': 'owned-local', 'database': 'owned-local',
                           'environment_binding_confirmed': True}
    profile['policy'].update(fixture_pool=scope, exclusive_use_confirmed=True)
    profile['file_transfer'] = {'allowed_storage_origins': [runtime.inputs.origin]}
    return validate(profile)


class OwnedLocalPilotAdapter(RemotePilotAdapter):
    """Same business transport/Stories; local-only auth and physical DB binding."""

    termination_mode = "LOCAL_CONTROLLED"
    def __init__(self, runtime: OwnedBcsProcess, profile: dict, *, state_dir: Path, case_id: str):
        self.profile = validate(profile)
        state = read(state_dir / 'run.json')
        digest = require_profile_binding(state, self.profile)
        if state['run_id'] != runtime.run_id or self.profile['observer']['kind'] != 'local-sqlite':
            raise RuntimeError('owned local adapter Run or observer binding mismatch')
        self.local_auth = OwnedLocalAuth(runtime, self.profile)
        self.observer = OwnedSqliteEvidenceProvider(runtime, ledger_path=Path(state['ledger_path']),
                                                    profile_digest=digest, case_id=case_id)
        self.read = ReadClient(self.profile, local_auth=self.local_auth)
        self.write = WriteClient(self.profile, local_auth=self.local_auth)
        self.files = RemoteFileClient(self.profile, local_auth=self.local_auth)
        self.websocket = SessionWebSocketClient(self.profile['entrypoint']['origin'],
                                                timeout_seconds=self.profile['policy']['timeout_seconds'])
        self.ws_connections = {}

    def semantic_observer(self):
        return self.observer

    def session_ws_connect(self, group_id: str, session_id: str, token: str):
        self.local_auth.require_bound(self.profile)
        value, record = super().session_ws_connect(group_id, session_id, token)
        record['source'] = 'owned-local-websocket'
        return value, record

    def session_ws_close(self, session_id: str):
        # Closing our held socket must still work after the server dies.
        value, record = super().session_ws_close(session_id)
        record['source'] = 'owned-local-websocket'
        return value, record
