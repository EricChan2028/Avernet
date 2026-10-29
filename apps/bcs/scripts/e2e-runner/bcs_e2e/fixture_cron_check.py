"""Explicit preproduction fixture read; no routine writes, execution or release."""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from .fixture_cron import project_crons
from .preflight import check
from .profile import ConfigError, fingerprint, identifier, origin, unique_keys
from .transport import ReadClient



CHECK_IDS = {'fixture.exclusive', 'observer.environment_binding', 'target.environment',
             'identity.human', 'route.business_adapter', 'actor.driver', 'actor.member', 'http.observation'}
OPERATIONS = {'environment.read', 'identity.read', 'identity.actor.read', 'bot.read'}


def _preflight_view(result: dict, records: list[dict]) -> dict:
    """Do not propagate arbitrary response IDs/text through the existing check."""
    checks = []
    for row in result.get('checks', [])[:8]:
        if isinstance(row, dict):
            checks.append({'id': row['id'] if isinstance(row.get('id'), str) and row['id'] in CHECK_IDS else 'UNKNOWN',
                           'status': 'PASS' if row.get('status') == 'PASS' else 'BLOCKED'})
    requests = []
    for row in records[:5]:
        operation, status = row.get('operation'), row.get('http_status')
        record = {'operation': operation if isinstance(operation, str) and operation in OPERATIONS else 'UNKNOWN',
                  'http_status': status if type(status) is int and 100 <= status <= 599 else None,
                  'request_id_echoed': row.get('request_id_echoed') is True}
        for name in ('submitted_request_id', 'response_request_id'):
            value = row.get(name)
            if isinstance(value, str):
                record[name+'_sha256'] = hashlib.sha256(value.encode('utf-8', errors='surrogatepass')).hexdigest()
        elapsed = row.get('elapsed_ms')
        if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
            record['elapsed_ms'] = elapsed
        requests.append(record)
    return {'status': result.get('status') if result.get('status') in ('DISCOVERY_PASSED', 'CANCELLED') else 'BLOCKED',
            'checks': checks, 'requests': requests, 'request_identity_projection': 'DIGESTS_ONLY'}

def fixture_cron_check(profile: dict, *, allow_read: bool, confirm_human_id: str, confirm_frontend_origin: str) -> dict:
    if (allow_read is not True or confirm_human_id != profile['human']['user_id']
            or profile['environment']['tier'] != 'preprod' or profile['entrypoint']['kind'] != 'gateway'
            or set(profile['auth'].get('header_env', {})) != {'Cookie'}):
        raise ConfigError('fixture cron check requires explicit preproduction gateway Human/Cookie reads')
    browser = origin(profile.get('browser_origin'), 'preprod')
    if confirm_frontend_origin != browser:
        raise ConfigError('fixture cron check requires explicit confirmation of the frontend credential destination')
    owner = profile['human']['user_id']
    targets = {}
    for role, actor in profile['actors'].items():
        # This adapter supports only the reviewed backend identity construction,
        # not arbitrary BCS/provider identities or a guessed different owner.
        parts = actor['bot_id'].split(':')
        if len(parts) != 2 or parts[1] != owner or parts[0].lower() == 'all':
            raise ConfigError('fixture cron check requires backend-Bot:owner actor bindings')
        targets[role] = identifier(parts[0], 'fixture backend Bot ID')
    if set(targets) != {'driver', 'member'} or len(set(targets.values())) != 2:
        raise ConfigError('fixture cron check requires two distinct bound roles')
    report = {'schema_version': 1, 'kind': 'FIXTURE_CRON_READ', 'status': 'BLOCKED',
              'profile_digest': fingerprint(profile), 'human_id': owner, 'actors': {}, 'requests': [],
              'maximum_http_calls': 7, 'runtime_inventory_completeness': 'NOT_VERIFIED',
              'backend_environment_verification': 'CONFIGURED_FRONTEND_ROUTE_ONLY',
              'input_boundary': 'NOT_PROVEN', 'pool_release_authorized': False, 'ready_to_execute': False,
              'runner_business_writes': 0, 'route_fallback': False,
              'downstream_side_effect_verification': 'BACKEND_READ_PATH_REVIEWED_NOT_END_TO_END_PROOF'}
    client = ReadClient(profile)
    try:
        preflight = check(profile, client)
    except KeyboardInterrupt:
        report.update(status='CANCELLED', preflight=_preflight_view({'status': 'CANCELLED'}, client.evidence))
        return report
    report['preflight'] = _preflight_view(preflight, client.evidence)
    if preflight['status'] != 'DISCOVERY_PASSED':
        return report
    for role, bot_id in targets.items():
        path = '/api/cron?' + urlencode({'bot_id': bot_id, 'owner_id': owner})
        request_id = 'e2e-cron-check-' + uuid.uuid4().hex
        request = Request(browser + path, method='GET', headers={**client.headers,
            'Accept': 'application/json', 'X-Request-ID': request_id})
        record = {'role': role, 'method': 'GET', 'path': path, 'submitted_request_id': request_id,
                  'http_status': None, 'status': 'STARTED'}
        report['requests'].append(record)
        start = time.monotonic()
        try:
            try:
                response = client.opener.open(request, timeout=profile['policy']['timeout_seconds'])
            except HTTPError as error:
                response = error
            with response:
                record['http_status'] = response.code
                if response.code != 200:
                    record['status'] = 'HTTP_REJECTED'
                    break  # No route/identity fallback, nor a second target after an HTTP rejection.
                media_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                record['response_media_type'] = media_type if media_type in ('application/json', 'text/html') else 'OTHER'
                if media_type != 'application/json':
                    record.update(status='UNSUPPORTED_RESPONSE', reason='EXPECTED_JSON_NOT_PAGE_CONTENT')
                    break
                raw = response.read(profile['policy']['max_response_bytes']+1)
                if len(raw) > profile['policy']['max_response_bytes']:
                    raise ConfigError('fixture cron response exceeds its byte budget')
                value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
                projected = project_crons(value, bot_id=bot_id, owner_id=owner)
            report['actors'][role] = projected
            record['status'] = projected['status']
            if projected['status'] == 'BLOCKED':
                break
        except KeyboardInterrupt:
            report['status'] = 'CANCELLED'
            record['status'] = 'CANCELLED'
            return report
        except (ConfigError, OSError, URLError, ValueError, UnicodeError, RecursionError) as error:
            record['status'] = 'UNAVAILABLE'
            record['error_type'] = type(error).__name__
            break
        finally:
            record['elapsed_ms'] = round((time.monotonic()-start)*1000, 2)
    if len(report['actors']) == 2:
        statuses = {v['status'] for v in report['actors'].values()}
        report['status'] = ('BLOCKED' if 'BLOCKED' in statuses else 'OBSERVED' if statuses == {'OBSERVED'}
                            else 'INCOMPLETE')
    return report
