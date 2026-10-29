"""Offline-only preprod adapter for the session owner permissions slice.

Reconnaissance conclusion (frozen at binding time): the preprod public-v1
gateway face cannot execute this Story honestly. The reviewed pilot bindings
cover only group/session CRUD, messages, session token and the network
register trio; participants PATCH/POST/DELETE, session collect add/remove,
view_bot_id-scoped session listing, message history authorization and Bot
visibility live on faces the pilot does not bind. The station legacy
bootstrap legs (ensure-human, register token, register, onboard) and
per-request X-Mock-User-Id admission exist only on the owned-local runtime.
The story needs three distinct Human principals (manager/owner/outsider) with
per-Human owned Bots, while the preprod pilot credential family is one Human
Cookie plus its dedicated registered Bots. Public-v1 X-Avernet-Principal
minting for arbitrary Humans needs an operator-provisioned
``auth.principal_signing_key_env`` reference the pilot profile does not
declare. This adapter therefore NEVER opens a transport: every operation
answers an honest NOT_RUN receipt carrying the matching documented gap.
"""
from __future__ import annotations

from pathlib import Path

from ..profile import fingerprint
from ..run_state import RunStateError
from .session_owner_contract import DOCUMENTED_GAPS

GAP_STATION_BOOTSTRAP = 'PREPROD_STATION_BOOTSTRAP_FACE_ABSENT'
GAP_HUMAN_FAMILY = 'PREPROD_HUMAN_PRINCIPAL_FAMILY_SINGLE'
GAP_PRINCIPAL_MINTING = 'PREPROD_PRINCIPAL_MINTING_OPERATOR_KEY'
GAP_PARTICIPANT_ROUTES = 'PREPROD_PARTICIPANT_ROUTES_UNBOUND'
GAP_IDS = (GAP_STATION_BOOTSTRAP, GAP_HUMAN_FAMILY, GAP_PRINCIPAL_MINTING, GAP_PARTICIPANT_ROUTES)

_ADMITTED = (('singlebox', 'local'), ('gateway', 'preprod'))


class SessionOwnerRemoteGap(RunStateError):
    """Raised only if a caller bypasses the offline NOT_RUN receipts."""


class RemoteSessionOwnerAdapter:
    """Preprod public-v1 adapter: offline admission plus honest gap receipts."""

    def __init__(self, profile: dict):
        if not isinstance(profile, dict):
            raise RunStateError('Session owner preprod adapter requires an admitted profile')
        entrypoint = profile.get('entrypoint', {})
        environment = profile.get('environment', {})
        admitted = (entrypoint.get('kind'), environment.get('tier')) in _ADMITTED
        if not admitted or entrypoint.get('api_face') != 'public-v1':
            raise RunStateError(
                'Session owner preprod adapter requires a public-v1 Singlebox-local or Gateway-preprod profile')
        self.profile = profile
        self.profile_digest = fingerprint(profile)
        self.entrypoint = {'kind': entrypoint.get('kind'), 'api_face': entrypoint.get('api_face'),
                           'tier': environment.get('tier'), 'environment_id': environment.get('id')}

    def _not_run(self, gap_id: str) -> dict:
        return {'status': 'NOT_RUN', 'ready_to_execute': False, 'remote_validation': 'NOT_RUN',
                'grants_release_credit': False, 'gap': gap_id, 'reason': DOCUMENTED_GAPS[gap_id]}

    def preflight(self) -> dict:
        return {'status': 'NOT_RUN', 'ready_to_execute': False, 'remote_validation': 'NOT_RUN',
                'grants_release_credit': False, 'entrypoint': dict(self.entrypoint),
                'profile_digest': self.profile_digest,
                'documented_gaps': dict(DOCUMENTED_GAPS), 'gap_ids': list(GAP_IDS)}

    # --- gap 3: operator-provisioned principal minting ----------------------

    def mint_principal(self, *, user_id: str, username: str) -> dict:
        return self._not_run(GAP_PRINCIPAL_MINTING)

    # --- gap 1: station legacy bootstrap face -------------------------------

    def ensure_human(self, user: str) -> dict:
        return self._not_run(GAP_STATION_BOOTSTRAP)

    def register_token(self, user: str) -> dict:
        return self._not_run(GAP_STATION_BOOTSTRAP)

    def register_bot(self, user: str, name: str, token: str) -> dict:
        return self._not_run(GAP_STATION_BOOTSTRAP)

    def onboard_bot(self, user: str, bot_uuid: str, bot_token: str) -> dict:
        return self._not_run(GAP_STATION_BOOTSTRAP)

    def set_bot_visibility(self, user: str, bot_uuid: str, visibility: str) -> dict:
        return self._not_run(GAP_STATION_BOOTSTRAP)

    def delete_bot(self, user: str, bot_uuid: str) -> dict:
        return self._not_run(GAP_STATION_BOOTSTRAP)

    # --- gap 2: three-Human ownership isolation matrix ----------------------

    def create_group(self, user: str, body: dict) -> dict:
        return self._not_run(GAP_HUMAN_FAMILY)

    def read_group(self, user: str, group_id: str) -> dict:
        return self._not_run(GAP_HUMAN_FAMILY)

    def patch_group(self, user: str, group_id: str, body: dict) -> dict:
        return self._not_run(GAP_HUMAN_FAMILY)

    def delete_group(self, user: str, group_id: str) -> dict:
        return self._not_run(GAP_HUMAN_FAMILY)

    def create_session(self, user: str, group_id: str, body: dict) -> dict:
        return self._not_run(GAP_HUMAN_FAMILY)

    def read_session(self, user: str, session_id: str) -> dict:
        return self._not_run(GAP_HUMAN_FAMILY)

    def patch_session(self, user: str, session_id: str, body: dict) -> dict:
        return self._not_run(GAP_HUMAN_FAMILY)

    def delete_session(self, user: str, session_id: str) -> dict:
        return self._not_run(GAP_HUMAN_FAMILY)

    # --- gap 4: participants/collect/listing/history routes unbound ---------

    def add_participant(self, user: str, session_id: str, bot_uuid: str) -> dict:
        return self._not_run(GAP_PARTICIPANT_ROUTES)

    def patch_participant(self, user: str, session_id: str, actor: str, body: dict) -> dict:
        return self._not_run(GAP_PARTICIPANT_ROUTES)

    def delete_participant(self, user: str, session_id: str, actor: str) -> dict:
        return self._not_run(GAP_PARTICIPANT_ROUTES)

    def list_sessions(self, user: str, group_id: str, view_bot_id: str) -> dict:
        return self._not_run(GAP_PARTICIPANT_ROUTES)

    def read_messages(self, user: str, session_id: str) -> dict:
        return self._not_run(GAP_PARTICIPANT_ROUTES)

    def collect_add(self, user: str, session_id: str, participant: str) -> dict:
        return self._not_run(GAP_PARTICIPANT_ROUTES)

    def collect_remove(self, user: str, session_id: str, participant: str) -> dict:
        return self._not_run(GAP_PARTICIPANT_ROUTES)
