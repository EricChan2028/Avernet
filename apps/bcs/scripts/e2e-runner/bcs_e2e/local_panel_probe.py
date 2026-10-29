"""Bounded exact manifest and artifact readback for a live owned local runtime."""
import hashlib
import json
from urllib.request import Request, ProxyHandler, build_opener

from .owned_bcs import OwnedBcsProcess, file_sha256
from .local_panel_asset import LocalPanelAsset
from .profile import unique_keys, fingerprint
from .run_state import read
from .runtime import RuntimeContractError
from .transport import NoRedirect


def verify_owned_panel(runtime):
    if type(runtime) is not OwnedBcsProcess or runtime.panel_asset is None:
        raise RuntimeContractError('manifest probe requires an explicit owned panel input')
    def active():
        if (runtime.state != 'RUNNING' or not runtime.ready or runtime.child is None
                or runtime.child.poll() is not None or not runtime._owns_listener(1)):
            raise RuntimeContractError('manifest probe runtime ownership is unavailable')
    active()
    config_digest=file_sha256(runtime.inputs.config_path)
    receipt=read(runtime.root/'panel-asset.json')
    receipt_digest=fingerprint(receipt)
    asset=runtime.root/'panel'/'index.umd.js'
    owned_asset=LocalPanelAsset(asset,runtime.panel_asset.sha256)
    owned_asset.validate()
    if (receipt.get('run_id') != runtime.run_id
            or receipt.get('contract')!='bcs-local-panel-asset-v1'
            or receipt.get('sha256')!=runtime.panel_asset.sha256
            or receipt.get('owned_path')!='panel/index.umd.js'
            or receipt.get('size')!=asset.stat().st_size or asset.is_symlink()
            or (runtime.root/'panel').is_symlink() or file_sha256(asset)!=runtime.panel_asset.sha256):
        raise RuntimeContractError('owned manifest artifact binding changed')
    config=json.loads(runtime.inputs.config_path.read_text(),object_pairs_hook=unique_keys)
    expected={'name':'bcsPanel','type':'file','file':str(asset)}
    if config.get('manifest')!={'schema_version':1,'bundles':[expected]}:
        raise RuntimeContractError('owned manifest configuration is not exact')
    opener=build_opener(ProxyHandler({}),NoRedirect())
    with opener.open(Request(runtime.inputs.origin+'/manifest',method='GET'),timeout=5) as response:
        raw=response.read(65_537)
        if response.status!=200 or len(raw)>65_536:
            raise RuntimeContractError('manifest response was not confirmed')
    manifest=json.loads(raw.decode(),object_pairs_hook=unique_keys)
    path='/assets/bcsPanel/index.umd.js'
    if (manifest.get('schema_version')!=1 or manifest.get('env')!='local'
            or manifest.get('bundles')!=[{'name':'bcsPanel','url':path}]):
        raise RuntimeContractError('manifest advertised an unexpected asset or environment')
    size=asset.stat().st_size
    if not 1000<size<=2*1024*1024:
        raise RuntimeContractError('manifest asset size is outside the approved range')
    with opener.open(Request(runtime.inputs.origin+path,method='GET'),timeout=5) as response:
        data=response.read(size+1)
        media=response.headers.get('Content-Type','').split(';',1)[0].strip().lower()
        if response.status!=200 or media!='application/javascript':
            raise RuntimeContractError('manifest asset response is not JavaScript')
    if len(data)!=size or hashlib.sha256(data).hexdigest()!=runtime.panel_asset.sha256:
        raise RuntimeContractError('manifest bytes differ from the approved artifact')
    active()
    owned_asset.validate()
    if (file_sha256(runtime.inputs.config_path)!=config_digest or file_sha256(asset)!=runtime.panel_asset.sha256
            or fingerprint(read(runtime.root/'panel-asset.json'))!=receipt_digest):
        raise RuntimeContractError('manifest input changed during readback')
    return {'status':'PASS','scope':'OWNED_MANIFEST_ASSET_ONLY','asset_path':path,
            'size_bytes':size,'sha256':runtime.panel_asset.sha256,
            'content_type':media,'environment':'local','full_story_status':'NOT_MIGRATED',
            'remote_validation':'NOT_RUN'}
