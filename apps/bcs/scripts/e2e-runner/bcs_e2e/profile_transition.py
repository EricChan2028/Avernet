"""Reviewed storage-origin addition for a serial retained scheduling handoff.

This is not authority to change identity, route, pool or database, or to send a
request to storage. Old Run files are never written. Expiry is checked for live
handoff, not when independently rendering a frozen historical receipt.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re
import time

from .profile import ConfigError, fingerprint, identifier, load, object_keys, validate
from .run_state import read

CONTRACT = 'retained-storage-profile-transition-v1'
FILENAME = 'profile-transition.json'


def validate_transition(review: dict, previous_profile: dict, profile: dict, *,
                        run_id: str, previous_run_id: str, live: bool) -> dict:
    old = validate(copy.deepcopy(previous_profile))
    new = validate(copy.deepcopy(profile))
    object_keys(review, {'schema_version', 'contract', 'run_id', 'previous_run_id',
        'previous_profile_digest', 'profile_digest', 'storage_origins', 'supervisor',
        'reviewed_at', 'expires_at', 'reference', 'evidence_sha256'}, 'profile transition review')
    identifier(run_id, 'new retained Run ID')
    identifier(previous_run_id, 'previous retained Run ID')
    if (type(review['schema_version']) is not int or review['schema_version'] != 1
            or review['contract'] != CONTRACT or review['run_id'] != run_id
            or review['previous_run_id'] != previous_run_id or run_id == previous_run_id
            or review['previous_profile_digest'] != fingerprint(old)
            or review['profile_digest'] != fingerprint(new)):
        raise ConfigError('profile transition does not bind the exact previous/new Run and profiles')
    previous_transfer = old.pop('file_transfer', {'allowed_storage_origins': []})
    transfer = new.pop('file_transfer', None)
    if (old != new or previous_transfer != {'allowed_storage_origins': []}
            or not transfer or not transfer['allowed_storage_origins']
            or review['storage_origins'] != transfer['allowed_storage_origins']):
        raise ConfigError('profile transition permits only a first reviewed storage-origin addition')
    if review['supervisor'] != profile['human']['user_id']:
        raise ConfigError('profile transition requires the unchanged bound Human supervisor')
    identifier(review['reference'], 'profile transition evidence reference')
    if not isinstance(review['evidence_sha256'], str) or re.fullmatch('[0-9a-f]{64}', review['evidence_sha256']) is None:
        raise ConfigError('profile transition requires reviewed storage evidence digest')
    start, end = review['reviewed_at'], review['expires_at']
    if (type(start) is not int or type(end) is not int or not 0 < start < end
            or end-start > 3600 or live and not start <= int(time.time()) < end):
        raise ConfigError('profile transition validity is invalid or expired')
    return {'schema_version': 1, 'contract': CONTRACT, 'review': copy.deepcopy(review),
            'previous_profile': copy.deepcopy(previous_profile)}


class ProfileTransition:
    """Current input snapshots, revalidated while holding the trigger guard."""
    def __init__(self, profile: dict, run_id: str, previous_profile_path: Path, review_path: Path):
        self.profile = copy.deepcopy(profile)
        self.run_id = run_id
        self.previous_profile_path, self.review_path = previous_profile_path, review_path
        self.previous_profile = load(previous_profile_path)
        self.review = read(review_path)
        self.previous_run_id = self.review.get('previous_run_id')
        self.snapshot = validate_transition(self.review, self.previous_profile, self.profile,
            run_id=run_id, previous_run_id=self.previous_run_id, live=True)

    def check(self, profile: dict, run_id: str, previous_run_id: str) -> dict:
        if (profile != self.profile or run_id != self.run_id or previous_run_id != self.previous_run_id
                or load(self.previous_profile_path) != self.previous_profile
                or read(self.review_path) != self.review):
            raise ConfigError('profile transition inputs changed; no automatic re-review')
        snapshot = validate_transition(self.review, self.previous_profile, profile,
            run_id=run_id, previous_run_id=previous_run_id, live=True)
        if snapshot != self.snapshot:
            raise ConfigError('profile transition snapshot changed')
        return copy.deepcopy(snapshot)


def frozen_transition(profile: dict, state_dir: Path, receipt: dict) -> dict:
    """Historical read requires only frozen artifacts, never source files/network."""
    snapshot = read(state_dir / FILENAME)
    object_keys(snapshot, {'schema_version', 'contract', 'review', 'previous_profile'}, 'frozen profile transition')
    if fingerprint(snapshot) != receipt.get('profile_transition_digest') or snapshot['contract'] != CONTRACT:
        raise ConfigError('frozen profile transition differs from serial admission')
    expected = validate_transition(snapshot['review'], snapshot['previous_profile'], profile,
        run_id=receipt['run_id'], previous_run_id=receipt['previous_run_id'], live=False)
    if snapshot != expected:
        raise ConfigError('frozen profile transition has invalid provenance')
    return {'profile_transition': CONTRACT,
            'previous_profile_digest': fingerprint(snapshot['previous_profile'])}
