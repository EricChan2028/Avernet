"""Semantic checks over non-credential Draft delivery observations.

The black-box child (``story.py``) emits schema-2 evidence: one raw observation
record per frozen ``DRAFT-DELIVERY.*`` obligation, the Draft HTTP response
matrix, and fixture identity hashes. These functions independently reject any
observation that contradicts its frozen obligation; they never inspect or
retain an authorization credential. Verified count: 32 obligations.
"""
from __future__ import annotations

import re

from ..run_state import RunStateError
from .contract import REDACTION_TOKENS, REQUIREMENTS

OBLIGATION_PREFIX = 'DRAFT-DELIVERY.'
REDACTION_STREAMS = ('story.stdout', 'story.stderr', 'owned.console')
REDACTION_ARTIFACTS = ('child.receipt', 'flow.report', 'canonical.report')
_HTTPS_STATUS_MATRIX = {200, 401, 404, 409}
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
_HEX64 = re.compile(r'^[0-9a-f]{64}$')


class DraftDeliveryViolation(RunStateError):
    """A live observation contradicted a frozen Draft delivery obligation."""


def _fail(obligation: str, detail: str) -> None:
    raise DraftDeliveryViolation(obligation + ': ' + detail)


def _mapping(value: object, field: str, obligation: str) -> dict:
    if not isinstance(value, dict):
        _fail(obligation, field + ' observation is missing')
    return value


def _flag(value: object, field: str, obligation: str) -> bool:
    if value is not True and value is not False:
        _fail(obligation, field + ' must be a boolean')
    return value


def _integer(value: object, field: str, obligation: str) -> int:
    if type(value) is not int:
        _fail(obligation, field + ' must be an integer')
    return value


def _text(value: object, field: str, obligation: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(obligation, field + ' must be a non-empty string')
    return value


def _record(evidence: dict, obligation: str) -> dict:
    obligations = _mapping(evidence.get('obligations'), 'obligations', obligation)
    if obligation not in obligations:
        _fail(obligation, 'observation record is missing')
    return _mapping(obligations.get(obligation), 'observation', obligation)


def _fixture(evidence: dict, obligation: str) -> dict:
    return _mapping(evidence.get('fixture'), 'fixture', obligation)


def _offline_cli(record: dict, obligation: str) -> None:
    if record.get('transport') != 'offline-cli':
        _fail(obligation, 'offline preview observation did not come from the product CLI')


def _product_cli(record: dict, obligation: str) -> None:
    if record.get('transport') != 'product-cli':
        _fail(obligation, 'observation did not come from the product CLI')


def _product_http(record: dict, obligation: str) -> None:
    if record.get('transport') != 'product-http':
        _fail(obligation, 'observation did not come from the product HTTP surface')


# ------------------------------------------------- O1-O3 offline schema-1 preview
def check_offline_preview_schema1_snapshot(evidence: dict) -> None:
    """Offline preview emits a schema-1 snapshot without a server round trip."""
    record = _record(evidence, 'DRAFT-DELIVERY.offline-preview-schema1-snapshot')
    _offline_cli(record, 'DRAFT-DELIVERY.offline-preview-schema1-snapshot')
    if _integer(record.get('exit_code'), 'exit code',
                'DRAFT-DELIVERY.offline-preview-schema1-snapshot') != 0:
        _fail('DRAFT-DELIVERY.offline-preview-schema1-snapshot', 'offline preview exited non-zero')
    if _flag(record.get('snapshot_present'), 'snapshot presence',
             'DRAFT-DELIVERY.offline-preview-schema1-snapshot') is not True:
        _fail('DRAFT-DELIVERY.offline-preview-schema1-snapshot',
              'offline preview did not emit a snapshot')


def check_offline_preview_retains_schema1(evidence: dict) -> None:
    """Offline preview retains the frozen schema-1 snapshot schemaVersion."""
    record = _record(evidence, 'DRAFT-DELIVERY.offline-preview-retains-schema1')
    _offline_cli(record, 'DRAFT-DELIVERY.offline-preview-retains-schema1')
    if _integer(record.get('schema_version'), 'schema version',
                'DRAFT-DELIVERY.offline-preview-retains-schema1') != 1:
        _fail('DRAFT-DELIVERY.offline-preview-retains-schema1',
              'offline preview did not retain schema 1')


def check_offline_preview_preserves_bytes(evidence: dict) -> None:
    """Offline preview reproduces the incident source bytes exactly."""
    record = _record(evidence, 'DRAFT-DELIVERY.offline-preview-preserves-bytes')
    _offline_cli(record, 'DRAFT-DELIVERY.offline-preview-preserves-bytes')
    if _integer(record.get('definition_yaml_bytes'), 'definition bytes',
                'DRAFT-DELIVERY.offline-preview-preserves-bytes') != 2174:
        _fail('DRAFT-DELIVERY.offline-preview-preserves-bytes',
              'offline preview did not observe the incident byte count')
    if _flag(record.get('previewed_bytes_equal'), 'preview byte equality',
             'DRAFT-DELIVERY.offline-preview-preserves-bytes') is not True:
        _fail('DRAFT-DELIVERY.offline-preview-preserves-bytes',
              'offline preview did not preserve the incident bytes')


# ------------------------------------------------- O4-O7 selected-ID create path
def check_author_upload_incident_file(evidence: dict) -> None:
    """Author product-CLI create uploads the incident file under the client UUID."""
    obligation = 'DRAFT-DELIVERY.author-upload-incident-file'
    record = _record(evidence, obligation)
    _product_cli(record, obligation)
    if _integer(record.get('exit_code'), 'exit code', obligation) != 0:
        _fail(obligation, 'author upload exited non-zero')
    if _flag(record.get('saved'), 'saved flag', obligation) is not True:
        _fail(obligation, 'author upload did not save')
    if record.get('phase') != 'ready':
        _fail(obligation, 'author upload did not complete the ready phase')
    draft_id = _text(record.get('draft_id'), 'draft ID', obligation)
    if draft_id != evidence.get('draft_id') or _UUID.fullmatch(draft_id) is None:
        _fail(obligation, 'create did not bind the client-selected UUID')
    if _flag(record.get('create_stdout_token_field_present'), 'create stdout redaction',
             obligation) is not False:
        _fail(obligation, 'create CLI output exposed the fixed-length card token field')


def check_saved_source_2174_bytes(evidence: dict) -> None:
    """Saved first revision keeps the exact incident byte count."""
    record = _record(evidence, 'DRAFT-DELIVERY.saved-source-2174-bytes')
    _product_cli(record, 'DRAFT-DELIVERY.saved-source-2174-bytes')
    if _integer(record.get('size_bytes'), 'size bytes',
                'DRAFT-DELIVERY.saved-source-2174-bytes') != 2174:
        _fail('DRAFT-DELIVERY.saved-source-2174-bytes', 'saved source is not 2174 bytes')


def check_saved_source_validates(evidence: dict) -> None:
    """Saved first revision passes server-side source validation."""
    record = _record(evidence, 'DRAFT-DELIVERY.saved-source-validates')
    _product_cli(record, 'DRAFT-DELIVERY.saved-source-validates')
    if _flag(record.get('valid'), 'validation flag',
             'DRAFT-DELIVERY.saved-source-validates') is not True:
        _fail('DRAFT-DELIVERY.saved-source-validates', 'saved source did not validate')


def check_first_revision_immutable(evidence: dict) -> None:
    """Replay is idempotent, foreign content conflicts, revision 1 never mutates."""
    obligation = 'DRAFT-DELIVERY.first-revision-immutable'
    record = _record(evidence, obligation)
    _product_cli(record, obligation)
    fixture = _fixture(evidence, obligation)
    if _integer(record.get('create_revision'), 'create revision', obligation) != 1:
        _fail(obligation, 'first create did not bind revision 1')
    if _integer(record.get('replay_exit_code'), 'replay exit code', obligation) != 0:
        _fail(obligation, 'exact create replay failed')
    if _integer(record.get('replay_revision'), 'replay revision', obligation) != 1:
        _fail(obligation, 'exact create replay renumbered the Draft')
    if _flag(record.get('replay_saved'), 'replay saved flag', obligation) is not True:
        _fail(obligation, 'exact create replay did not save revision 1')
    # bcs-cli attributes matched_existing_revision only to fixed
    # next-revision update recovery; a create replay keeps it false.
    if _flag(record.get('replay_matched_existing'), 'replay match flag', obligation) is not False:
        _fail(obligation, 'exact create replay was not idempotent')
    if record.get('replay_content_hash') != fixture.get('content_hash'):
        _fail(obligation, 'replay content hash drifted from the incident hash')
    if _integer(record.get('conflict_exit_code'), 'conflict exit code', obligation) == 0:
        _fail(obligation, 'different content for the selected ID did not fail create')
    if _integer(record.get('conflict_http_status'), 'conflict HTTP status', obligation) != 409:
        _fail(obligation, 'conflicting create did not surface HTTP 409')
    if _integer(record.get('history_revision'), 'history revision', obligation) != 1:
        _fail(obligation, 'revision 1 was renumbered after probes')
    if _integer(record.get('history_size_bytes'), 'history size', obligation) != 2174:
        _fail(obligation, 'revision 1 byte count changed after probes')
    if record.get('history_content_hash') != fixture.get('content_hash'):
        _fail(obligation, 'revision 1 content mutated after replay and conflict probes')


# ------------------------------------------------- O8-O15 metadata and public card face
def check_author_reads_metadata(evidence: dict) -> None:
    """The author credential reads disposable Draft metadata."""
    record = _record(evidence, 'DRAFT-DELIVERY.author-reads-metadata')
    _product_http(record, 'DRAFT-DELIVERY.author-reads-metadata')
    if _integer(record.get('status'), 'status', 'DRAFT-DELIVERY.author-reads-metadata') != 200:
        _fail('DRAFT-DELIVERY.author-reads-metadata', 'author metadata read failed')


def check_metadata_head_revision_one(evidence: dict) -> None:
    """Metadata pins an undeleted revision-1 head."""
    record = _record(evidence, 'DRAFT-DELIVERY.metadata-head-revision-one')
    _product_http(record, 'DRAFT-DELIVERY.metadata-head-revision-one')
    if _integer(record.get('head_revision'), 'head revision',
                'DRAFT-DELIVERY.metadata-head-revision-one') != 1:
        _fail('DRAFT-DELIVERY.metadata-head-revision-one', 'metadata head is not revision 1')
    if _flag(record.get('deleted'), 'deleted flag',
             'DRAFT-DELIVERY.metadata-head-revision-one') is not False:
        _fail('DRAFT-DELIVERY.metadata-head-revision-one', 'metadata reports a deleted Draft')


def check_card_holder_tokenless_read(evidence: dict) -> None:
    """A card holder reads revision 1 with ID plus revision only, no credential."""
    record = _record(evidence, 'DRAFT-DELIVERY.card-holder-tokenless-read')
    _product_http(record, 'DRAFT-DELIVERY.card-holder-tokenless-read')
    if _integer(record.get('status'), 'status',
                'DRAFT-DELIVERY.card-holder-tokenless-read') != 200:
        _fail('DRAFT-DELIVERY.card-holder-tokenless-read', 'tokenless fixed read failed')


def check_shared_read_preserves_hash(evidence: dict) -> None:
    """Tokenless fixed read returns exactly the incident bytes and hash."""
    obligation = 'DRAFT-DELIVERY.shared-read-preserves-hash'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if record.get('content_hash') != _fixture(evidence, obligation).get('content_hash'):
        _fail(obligation, 'tokenless fixed read changed the content hash')
    if _integer(record.get('size_bytes'), 'size bytes', obligation) != 2174:
        _fail(obligation, 'tokenless fixed read changed the incident byte count')


def check_shared_source_no_store(evidence: dict) -> None:
    """Every shared fixed read response is uncacheable."""
    record = _record(evidence, 'DRAFT-DELIVERY.shared-source-no-store')
    _product_http(record, 'DRAFT-DELIVERY.shared-source-no-store')
    cache_control = record.get('cache_control')
    if not isinstance(cache_control, str) or 'no-store' not in cache_control.lower():
        _fail('DRAFT-DELIVERY.shared-source-no-store', 'shared read was cacheable')


def check_card_holder_validates_source(evidence: dict) -> None:
    """A card holder validates revision 1 without any credential."""
    record = _record(evidence, 'DRAFT-DELIVERY.card-holder-validates-source')
    _product_http(record, 'DRAFT-DELIVERY.card-holder-validates-source')
    if _integer(record.get('status'), 'status',
                'DRAFT-DELIVERY.card-holder-validates-source') != 200:
        _fail('DRAFT-DELIVERY.card-holder-validates-source',
              'tokenless validation request failed')


def check_shared_validation_binds_rev1(evidence: dict) -> None:
    """Tokenless validation binds revision 1 and returns valid."""
    obligation = 'DRAFT-DELIVERY.shared-validation-binds-rev1'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if _integer(record.get('revision'), 'revision', obligation) != 1:
        _fail(obligation, 'shared validation did not bind revision 1')
    if _flag(record.get('valid'), 'validation result', obligation) is not True:
        _fail(obligation, 'shared validation did not report the source valid')


def check_public_cannot_delete(evidence: dict) -> None:
    """Public readers and inert bearer-shaped tokens cannot manage the Draft."""
    obligation = 'DRAFT-DELIVERY.public-cannot-delete'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if _integer(record.get('public_delete_status'), 'public delete status',
                obligation) != 401:
        _fail(obligation, 'public delete was not rejected as unauthorized')
    if _integer(record.get('inert_delete_status'), 'inert delete status',
                obligation) not in (401, 403):
        _fail(obligation, 'an inert bearer-shaped card token delete was not rejected')
    if _integer(record.get('inert_update_status'), 'inert update status',
                obligation) not in (401, 403):
        _fail(obligation, 'an inert bearer-shaped card token append was not rejected')
    if _integer(record.get('head_after'), 'head after rejection', obligation) != 1:
        _fail(obligation, 'rejected management operation advanced the head')
    if _flag(record.get('deleted_after'), 'deleted after rejection', obligation) is not False:
        _fail(obligation, 'rejected management operation deleted the Draft')


# ------------------------------------------------- O16-O18 fixed-revision downloads
def check_author_download_fixed_bytes(evidence: dict) -> None:
    """Product CLI downloads revision 1 to a receipt-referenced file."""
    record = _record(evidence, 'DRAFT-DELIVERY.author-download-fixed-bytes')
    _product_cli(record, 'DRAFT-DELIVERY.author-download-fixed-bytes')
    if _integer(record.get('exit_code'), 'exit code',
                'DRAFT-DELIVERY.author-download-fixed-bytes') != 0:
        _fail('DRAFT-DELIVERY.author-download-fixed-bytes',
              'fixed-revision download exited non-zero')
    if _flag(record.get('output_path_in_receipt'), 'output receipt flag',
             'DRAFT-DELIVERY.author-download-fixed-bytes') is not True:
        _fail('DRAFT-DELIVERY.author-download-fixed-bytes',
              'download receipt did not reference the output file')


def check_download_matches_incident_bytes(evidence: dict) -> None:
    """Downloaded revision-1 bytes equal the incident file byte for byte."""
    record = _record(evidence, 'DRAFT-DELIVERY.download-matches-incident-bytes')
    _product_cli(record, 'DRAFT-DELIVERY.download-matches-incident-bytes')
    if _flag(record.get('bytes_equal'), 'byte equality',
             'DRAFT-DELIVERY.download-matches-incident-bytes') is not True:
        _fail('DRAFT-DELIVERY.download-matches-incident-bytes',
              'downloaded bytes differ from the incident file')
    if _integer(record.get('size_bytes'), 'size bytes',
                'DRAFT-DELIVERY.download-matches-incident-bytes') != 2174:
        _fail('DRAFT-DELIVERY.download-matches-incident-bytes',
              'downloaded byte count differs from the incident file')


def check_author_validates_fixed_revision(evidence: dict) -> None:
    """Product CLI validates the fixed revision and reports it valid."""
    record = _record(evidence, 'DRAFT-DELIVERY.author-validates-fixed-revision')
    _product_cli(record, 'DRAFT-DELIVERY.author-validates-fixed-revision')
    if _integer(record.get('exit_code'), 'exit code',
                'DRAFT-DELIVERY.author-validates-fixed-revision') != 0:
        _fail('DRAFT-DELIVERY.author-validates-fixed-revision',
              'fixed-revision CLI validation exited non-zero')
    if _flag(record.get('valid'), 'validation result',
             'DRAFT-DELIVERY.author-validates-fixed-revision') is not True:
        _fail('DRAFT-DELIVERY.author-validates-fixed-revision',
              'fixed-revision CLI validation did not pass')


# ------------------------------------------------- O19-O20 CAS append
def check_author_appends_revision(evidence: dict) -> None:
    """Author append with expected_revision 1 saves and reaches ready."""
    obligation = 'DRAFT-DELIVERY.author-appends-revision'
    record = _record(evidence, obligation)
    _product_cli(record, obligation)
    if _integer(record.get('exit_code'), 'exit code', obligation) != 0:
        _fail(obligation, 'revision append exited non-zero')
    if _flag(record.get('saved'), 'saved flag', obligation) is not True:
        _fail(obligation, 'revision append did not save')
    if record.get('phase') != 'ready':
        _fail(obligation, 'revision append did not complete the ready phase')


def check_update_advances_exactly_once(evidence: dict) -> None:
    """CAS append advances the head exactly once to revision 2."""
    obligation = 'DRAFT-DELIVERY.update-advances-exactly-once'
    record = _record(evidence, obligation)
    if record.get('transport') != 'product-cli':
        _fail(obligation, 'append observation did not come from the product CLI')
    if _integer(record.get('revision'), 'append revision', obligation) != 2:
        _fail(obligation, 'append did not create revision 2')
    if _integer(record.get('head_after'), 'head after append', obligation) != 2:
        _fail(obligation, 'append did not advance the head to revision 2')
    if record.get('latest_content_hash') != _fixture(evidence, obligation).get(
            'updated_content_hash'):
        _fail(obligation, 'revision 2 content hash does not bind the appended source')


# ------------------------------------------------- O21-O23 historical card reissue
def check_author_reissues_historical_card(evidence: dict) -> None:
    """Offline preview reissues a card pinned to revision 1."""
    obligation = 'DRAFT-DELIVERY.author-reissues-historical-card'
    record = _record(evidence, obligation)
    _product_cli(record, obligation)
    if _integer(record.get('exit_code'), 'exit code', obligation) != 0:
        _fail(obligation, 'historical card reissue exited non-zero')
    if record.get('phase') != 'ready':
        _fail(obligation, 'historical card reissue did not complete')
    reference = _mapping(record.get('reference'), 'reference', obligation)
    if reference.get('draft_id') != evidence.get('draft_id'):
        _fail(obligation, 'reissued CLI card bound a different Draft ID')
    if _integer(reference.get('revision'), 'CLI card revision', obligation) != 1:
        _fail(obligation, 'reissued CLI card did not stay on revision 1')
    if reference.get('http_draft_id') != evidence.get('draft_id'):
        _fail(obligation, 'reissued HTTP card bound a different Draft ID')
    if _integer(reference.get('http_revision'), 'HTTP card revision', obligation) != 1:
        _fail(obligation, 'reissued HTTP card did not stay on revision 1')
    if _flag(reference.get('card_token_field_present'), 'card token presence',
             obligation) is not True:
        _fail(obligation, 'reissued card did not carry its read token field')
    if _integer(reference.get('card_token_field_length'), 'card token length',
                obligation) != 43:
        _fail(obligation, 'reissued card did not carry a fixed-length read token field')


def check_historical_preview_original_bytes(evidence: dict) -> None:
    """The reissued historical card still describes the original 2174 source."""
    record = _record(evidence, 'DRAFT-DELIVERY.historical-preview-original-bytes')
    _product_cli(record, 'DRAFT-DELIVERY.historical-preview-original-bytes')
    if _integer(record.get('size_bytes'), 'size bytes',
                'DRAFT-DELIVERY.historical-preview-original-bytes') != 2174:
        _fail('DRAFT-DELIVERY.historical-preview-original-bytes',
              'historical preview did not return the original byte count')


def check_reissued_card_fixed_version(evidence: dict) -> None:
    """The reissued card pins schemaVersion 2 + revision 1; revision 2 keeps its own."""
    obligation = 'DRAFT-DELIVERY.reissued-card-fixed-version'
    record = _record(evidence, obligation)
    _product_cli(record, obligation)
    if _integer(record.get('reference_revision'), 'card revision', obligation) != 1:
        _fail(obligation, 'reissued card did not pin revision 1')
    if _integer(record.get('reference_schema_version'), 'card schema version',
                obligation) != 2:
        _fail(obligation, 'reissued card did not pin schemaVersion 2')
    if _integer(record.get('other_revision_card_revision'), 'other card revision',
                obligation) != 2:
        _fail(obligation, 'revision 2 card did not keep its own fixed revision')


# ------------------------------------------------- O24 stale CAS writer
def check_stale_write_rejected(evidence: dict) -> None:
    """A stale expected_revision write fails 409 and never displaces revision 2."""
    obligation = 'DRAFT-DELIVERY.stale-write-rejected'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if _integer(record.get('status'), 'status', obligation) != 409:
        _fail(obligation, 'stale writer did not get HTTP 409')
    if record.get('error') != 'DRAFT_CONFLICT':
        _fail(obligation, 'stale writer did not report DRAFT_CONFLICT')
    if _integer(record.get('head_after'), 'head after rejection', obligation) != 2:
        _fail(obligation, 'stale writer advanced or renumbered the head')
    if record.get('latest_content_hash') != _fixture(evidence, obligation).get(
            'updated_content_hash'):
        _fail(obligation, 'stale writer replaced the newer revision content')


# ------------------------------------------------- O25-O28 scoped revocation
def check_revoke_second_revision(evidence: dict) -> None:
    """Author revokes exactly revision 2 through the product CLI."""
    record = _record(evidence, 'DRAFT-DELIVERY.revoke-second-revision')
    _product_cli(record, 'DRAFT-DELIVERY.revoke-second-revision')
    if _integer(record.get('exit_code'), 'exit code',
                'DRAFT-DELIVERY.revoke-second-revision') != 0:
        _fail('DRAFT-DELIVERY.revoke-second-revision', 'revocation exited non-zero')
    if _flag(record.get('revoked'), 'revoked flag',
             'DRAFT-DELIVERY.revoke-second-revision') is not True:
        _fail('DRAFT-DELIVERY.revoke-second-revision', 'revocation did not complete')


def check_revoke_idempotent(evidence: dict) -> None:
    """Repeated revocation of the same revision stays idempotent."""
    record = _record(evidence, 'DRAFT-DELIVERY.revoke-idempotent')
    _product_cli(record, 'DRAFT-DELIVERY.revoke-idempotent')
    if _integer(record.get('exit_code'), 'exit code',
                'DRAFT-DELIVERY.revoke-idempotent') != 0:
        _fail('DRAFT-DELIVERY.revoke-idempotent', 'repeated revocation exited non-zero')
    if _flag(record.get('revoked'), 'revoked flag',
             'DRAFT-DELIVERY.revoke-idempotent') is not True:
        _fail('DRAFT-DELIVERY.revoke-idempotent', 'repeated revocation was not idempotent')


def check_revoked_card_unavailable(evidence: dict) -> None:
    """A revoked card reads back 404 with the frozen unavailability error."""
    obligation = 'DRAFT-DELIVERY.revoked-card-unavailable'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if _integer(record.get('status'), 'status', obligation) != 404:
        _fail(obligation, 'revoked card did not read back 404')
    if record.get('error') != 'DRAFT_REFERENCE_UNAVAILABLE':
        _fail(obligation, 'revoked card leaked a different error')


def check_old_shared_revision_readable(evidence: dict) -> None:
    """Revoking revision 2 leaves the shared revision-1 card fully readable."""
    obligation = 'DRAFT-DELIVERY.old-shared-revision-readable'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if _integer(record.get('status'), 'status', obligation) != 200:
        _fail(obligation, 'revoking revision 2 made revision 1 unreadable')
    if record.get('content_hash') != _fixture(evidence, obligation).get('content_hash'):
        _fail(obligation, 'revoking revision 2 changed revision 1 content')
    if _integer(record.get('size_bytes'), 'size bytes', obligation) != 2174:
        _fail(obligation, 'revoking revision 2 changed the revision-1 byte count')
    cache_control = record.get('cache_control')
    if not isinstance(cache_control, str) or 'no-store' not in cache_control.lower():
        _fail(obligation, 'sibling fixed read was cacheable')


# ------------------------------------------------- O29-O31 deletion tombstone
def check_author_deletes_draft(evidence: dict) -> None:
    """Author deletes the disposable Draft through the product CLI."""
    record = _record(evidence, 'DRAFT-DELIVERY.author-deletes-draft')
    _product_cli(record, 'DRAFT-DELIVERY.author-deletes-draft')
    if _integer(record.get('exit_code'), 'exit code',
                'DRAFT-DELIVERY.author-deletes-draft') != 0:
        _fail('DRAFT-DELIVERY.author-deletes-draft', 'deletion exited non-zero')
    if _flag(record.get('deleted'), 'deleted flag',
             'DRAFT-DELIVERY.author-deletes-draft') is not True:
        _fail('DRAFT-DELIVERY.author-deletes-draft', 'deletion did not complete')


def check_deletion_revokes_all_cards(evidence: dict) -> None:
    """Deletion tombstone revokes every historical card on the shared surface."""
    obligation = 'DRAFT-DELIVERY.deletion-revokes-all-cards'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if _integer(record.get('revision_one_status'), 'revision 1 status',
                obligation) != 404:
        _fail(obligation, 'deleted revision 1 card remained readable')
    if _integer(record.get('revision_two_status'), 'revision 2 status',
                obligation) != 404:
        _fail(obligation, 'deleted revision 2 card remained readable')
    metadata_status = _integer(record.get('metadata_status'), 'metadata status', obligation)
    deleted_flag = record.get('deleted_flag')
    if metadata_status == 200 and deleted_flag is not True:
        _fail(obligation, 'tombstone metadata survived without the deleted flag')
    if metadata_status not in (200, 404):
        _fail(obligation, 'post-deletion metadata status is invalid')


def check_late_create_cannot_revive(evidence: dict) -> None:
    """A late create against the tombstone cannot resurrect the Draft."""
    obligation = 'DRAFT-DELIVERY.late-create-cannot-revive'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if record.get('method') != 'PUT':
        _fail(obligation, 'late-create guard did not observe the create method')
    if _integer(record.get('status'), 'status', obligation) != 404:
        _fail(obligation, 'late create was not rejected as unavailable')
    if record.get('error') != 'DRAFT_REFERENCE_UNAVAILABLE':
        _fail(obligation, 'late create leaked a different error')


# ------------------------------------------------- O32 journey hygiene
def check_journey_completes(evidence: dict) -> None:
    """The full journey completes with no-store responses and canary-free observation."""
    obligation = 'DRAFT-DELIVERY.journey-completes'
    record = _record(evidence, obligation)
    _product_http(record, obligation)
    if _flag(record.get('responses_no_store'), 'no-store coverage', obligation) is not True:
        _fail(obligation, 'a Draft response omitted Cache-Control: no-store')
    matrix = record.get('status_matrix')
    if not isinstance(matrix, list) or not all(
            type(item) is int for item in matrix):
        _fail(obligation, 'response status matrix is malformed')
    if not _HTTPS_STATUS_MATRIX.issubset(set(matrix)):
        _fail(obligation, 'response matrix omitted success or error status coverage')
    if _flag(record.get('token_canary_free'), 'token canary flag', obligation) is not True:
        _fail(obligation, 'response observation captured credential-shaped material')


_CHECK_REGISTRY = {
    'DRAFT-DELIVERY.offline-preview-schema1-snapshot': check_offline_preview_schema1_snapshot,
    'DRAFT-DELIVERY.offline-preview-retains-schema1': check_offline_preview_retains_schema1,
    'DRAFT-DELIVERY.offline-preview-preserves-bytes': check_offline_preview_preserves_bytes,
    'DRAFT-DELIVERY.author-upload-incident-file': check_author_upload_incident_file,
    'DRAFT-DELIVERY.saved-source-2174-bytes': check_saved_source_2174_bytes,
    'DRAFT-DELIVERY.saved-source-validates': check_saved_source_validates,
    'DRAFT-DELIVERY.first-revision-immutable': check_first_revision_immutable,
    'DRAFT-DELIVERY.author-reads-metadata': check_author_reads_metadata,
    'DRAFT-DELIVERY.metadata-head-revision-one': check_metadata_head_revision_one,
    'DRAFT-DELIVERY.card-holder-tokenless-read': check_card_holder_tokenless_read,
    'DRAFT-DELIVERY.shared-read-preserves-hash': check_shared_read_preserves_hash,
    'DRAFT-DELIVERY.shared-source-no-store': check_shared_source_no_store,
    'DRAFT-DELIVERY.card-holder-validates-source': check_card_holder_validates_source,
    'DRAFT-DELIVERY.shared-validation-binds-rev1': check_shared_validation_binds_rev1,
    'DRAFT-DELIVERY.public-cannot-delete': check_public_cannot_delete,
    'DRAFT-DELIVERY.author-download-fixed-bytes': check_author_download_fixed_bytes,
    'DRAFT-DELIVERY.download-matches-incident-bytes': check_download_matches_incident_bytes,
    'DRAFT-DELIVERY.author-validates-fixed-revision': check_author_validates_fixed_revision,
    'DRAFT-DELIVERY.author-appends-revision': check_author_appends_revision,
    'DRAFT-DELIVERY.update-advances-exactly-once': check_update_advances_exactly_once,
    'DRAFT-DELIVERY.author-reissues-historical-card': check_author_reissues_historical_card,
    'DRAFT-DELIVERY.historical-preview-original-bytes': check_historical_preview_original_bytes,
    'DRAFT-DELIVERY.reissued-card-fixed-version': check_reissued_card_fixed_version,
    'DRAFT-DELIVERY.stale-write-rejected': check_stale_write_rejected,
    'DRAFT-DELIVERY.revoke-second-revision': check_revoke_second_revision,
    'DRAFT-DELIVERY.revoke-idempotent': check_revoke_idempotent,
    'DRAFT-DELIVERY.revoked-card-unavailable': check_revoked_card_unavailable,
    'DRAFT-DELIVERY.old-shared-revision-readable': check_old_shared_revision_readable,
    'DRAFT-DELIVERY.author-deletes-draft': check_author_deletes_draft,
    'DRAFT-DELIVERY.deletion-revokes-all-cards': check_deletion_revokes_all_cards,
    'DRAFT-DELIVERY.late-create-cannot-revive': check_late_create_cannot_revive,
    'DRAFT-DELIVERY.journey-completes': check_journey_completes,
}


def _envelope(evidence: dict) -> None:
    if not isinstance(evidence, dict):
        raise DraftDeliveryViolation('Draft delivery evidence must be an object')
    if type(evidence.get('schema_version')) is not int or evidence.get('schema_version') != 2:
        raise DraftDeliveryViolation('Draft delivery evidence envelope must be schema_version 2')
    draft_id = evidence.get('draft_id')
    if not isinstance(draft_id, str) or _UUID.fullmatch(draft_id) is None:
        raise DraftDeliveryViolation('Draft delivery evidence lacks the canonical Draft UUID')
    if evidence.get('identity_mode') not in ('register', 'bearer'):
        raise DraftDeliveryViolation('Draft delivery evidence identity mode is invalid')
    fixture = _mapping(evidence.get('fixture'), 'fixture', 'DRAFT-DELIVERY.journey-completes')
    for field in ('content_hash', 'updated_content_hash'):
        value = fixture.get(field)
        if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
            raise DraftDeliveryViolation('Draft delivery fixture hash observation is invalid')
    if fixture.get('content_hash') == fixture.get('updated_content_hash'):
        raise DraftDeliveryViolation('Draft delivery fixture revisions did not differ')
    if _integer(fixture.get('size_bytes'), 'fixture size bytes',
                'DRAFT-DELIVERY.journey-completes') != 2174:
        raise DraftDeliveryViolation('Draft delivery fixture byte observation is invalid')
    responses = evidence.get('responses')
    if not isinstance(responses, list):
        raise DraftDeliveryViolation('Draft delivery response matrix is missing')
    obligations = evidence.get('obligations')
    if not isinstance(obligations, dict) or set(obligations) != set(REQUIREMENTS):
        raise DraftDeliveryViolation('Draft delivery obligations do not match the frozen map')


OBLIGATION_CHECKS = tuple(
    (obligation, _CHECK_REGISTRY[obligation]) for obligation in REQUIREMENTS
)


def verify_all_obligations(evidence: dict) -> dict:
    """Run every independent obligation check and return a non-secret receipt."""
    _envelope(evidence)
    for obligation, check in OBLIGATION_CHECKS:
        if _CHECK_REGISTRY.get(obligation) is None:
            raise DraftDeliveryViolation(obligation + ': no verification function registered')
        try:
            check(evidence)
        except DraftDeliveryViolation:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise DraftDeliveryViolation(obligation + ': malformed observation') from None
    return {'status': 'PASS', 'checked': list(REQUIREMENTS)}


def scan_redaction(name: str, payload: bytes, *,
                   tokens: tuple[str, ...] = REDACTION_TOKENS) -> dict:
    """Return a non-secret observation of whether a byte stream leaks a canary."""
    if not isinstance(name, str) or not name:
        _fail('DRAFT-DELIVERY.token-redaction', 'redaction scan name is invalid')
    if not isinstance(payload, bytes):
        _fail('DRAFT-DELIVERY.token-redaction', 'redaction scan payload must be bytes')
    return {'name': name, 'clean': all(token.encode('utf-8') not in payload
                                       for token in tokens),
            'bytes': len(payload)}
