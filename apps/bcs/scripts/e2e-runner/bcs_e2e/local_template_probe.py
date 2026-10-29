"""Two fixed catalog reads from an owned BCS; not a migrated parent Story."""
import hashlib
from http.client import HTTPConnection
import json
import time
from urllib.parse import urlsplit

from .local_template_asset import CONTRACT, LocalTemplateAsset
from .owned_bcs import OwnedBcsProcess, file_sha256
from .profile import fingerprint, unique_keys
from .run_state import read
from .runtime import RuntimeContractError

PATHS = ('/collaboration/templates?lang=zh-CN',
         '/collaboration/templates/single-bot-guided-answer?lang=zh-CN&format=json')
TEMPLATE = 'single-bot-guided-answer'


def _require(value, message):
    if not value:
        raise RuntimeContractError(message)


def _get(port, path):
    """Fixed-origin HTTP, bounded bytes/deadline, no proxy/redirect/auth fallback."""
    _require(path in PATHS, 'template probe path is not admitted')
    connection = HTTPConnection('127.0.0.1', port, timeout=5)
    deadline = time.monotonic() + 5
    try:
        connection.request('GET', path, headers={'Accept': 'application/json'})
        sock = connection.sock
        response = connection.getresponse()
        raw = bytearray()
        with response:
            _require(response.status == 200, 'template read did not return HTTP 200')
            _require(response.headers.get('Content-Type', '').split(';')[0].strip() == 'application/json',
                     'template read did not return JSON')
            while True:
                remaining = deadline - time.monotonic()
                _require(remaining > 0, 'template read deadline exceeded')
                if sock is not None:
                    sock.settimeout(remaining)
                block = response.read1(min(4096, 262145 - len(raw)))
                if not block:
                    break
                raw.extend(block)
                _require(len(raw) <= 262144, 'template response exceeds byte budget')
            _require(response.length in (None, 0), 'template response was truncated')
        try:
            return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
        except (ValueError, UnicodeError):
            raise RuntimeContractError('template response is not valid unique-key JSON') from None
    finally:
        connection.close()


def verify_owned_templates(runtime):
    _require(type(runtime) is OwnedBcsProcess and type(runtime.template_asset) is LocalTemplateAsset,
             'template probe requires an explicit owned template input')
    asset = runtime.template_asset
    config_digest = file_sha256(runtime.inputs.config_path)
    receipt_path = runtime.root / 'template-assets.json'
    _require(not receipt_path.is_symlink(), 'template receipt cannot be a symlink')
    receipt = read(receipt_path)
    receipt_digest = fingerprint(receipt)
    approved = asset._approved_files()
    expected = {'schema_version': 1, 'run_id': runtime.run_id, 'contract': CONTRACT,
                'manifest_sha256': asset.manifest_sha256,
                'files': [{'path': item.path.as_posix(), 'sha256': item.sha256, 'size': item.size}
                          for item in approved]}
    owned = LocalTemplateAsset(runtime.root / 'templates', asset.manifest_path, asset.manifest_sha256)
    target = urlsplit(runtime.inputs.origin)
    _require(target.scheme == 'http' and target.hostname == '127.0.0.1' and target.port
             and target.netloc == '127.0.0.1:' + str(target.port) and not target.path
             and not target.query and not target.fragment, 'template runtime origin is not exact loopback')

    def active():
        _require(runtime.state == 'RUNNING' and runtime.ready and runtime.child is not None
                 and runtime.child.poll() is None and runtime._owns_listener(1),
                 'template probe lost live child ownership')
        _require(not runtime.root.is_symlink() and not receipt_path.is_symlink()
                 and receipt == expected and read(receipt_path) == expected
                 and fingerprint(read(receipt_path)) == receipt_digest
                 and file_sha256(runtime.inputs.config_path) == config_digest,
                 'template config or receipt changed')
        config = json.loads(runtime.inputs.config_path.read_text(), object_pairs_hook=unique_keys)
        _require(config.get('collaboration', {}).get('templates') == {
            'storage_type': 'file', 'base_dir': str(runtime.root / 'templates')},
            'template directory binding is not the owned copy')
        asset.validate()
        owned.validate()

    active()
    catalog = _get(target.port, PATHS[0])
    _require(isinstance(catalog, dict) and isinstance(catalog.get('templates'), list),
             'template catalog shape differs')
    rows = catalog['templates']
    _require(1 <= len(rows) <= 128 and all(isinstance(row, dict) for row in rows),
             'template catalog is empty or exceeds item budget')
    ids = [row.get('id') for row in rows]
    expected_ids = sorted(item.path.stem for item in approved if item.path.parts[0] == 'zh-CN')
    _require(all(isinstance(value, str) for value in ids) and sorted(ids) == expected_ids
             and TEMPLATE in ids and set(catalog.get('supported_languages', [])) == {'zh-CN', 'en-US'},
             'served template catalog differs from approved assets')
    active()
    detail = _get(target.port, PATHS[1])
    expected_yaml = (runtime.root / 'templates/zh-CN/single-bot-guided-answer.yaml').read_text(encoding='utf-8')
    _require(isinstance(detail, dict) and detail.get('id') == TEMPLATE and detail.get('lang') == 'zh-CN'
             and detail.get('yaml') == expected_yaml and bool(expected_yaml),
             'served template detail differs from approved YAML')
    active()
    return {'status': 'PASS', 'scope': 'OWNED_TEMPLATE_CATALOG_ONLY',
            'run_id': runtime.run_id, 'manifest_sha256': asset.manifest_sha256,
            'catalog_ids': ids, 'language': 'zh-CN', 'template_id': TEMPLATE,
            'yaml_sha256': hashlib.sha256(expected_yaml.encode()).hexdigest(),
            'operations': [{'method': 'GET', 'path': path, 'http_status': 200} for path in PATHS],
            'full_story_status': 'NOT_MIGRATED', 'remote_validation': 'NOT_RUN'}
