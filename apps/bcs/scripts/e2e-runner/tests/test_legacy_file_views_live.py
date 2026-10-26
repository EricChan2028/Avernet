"""Actual native reads over a V1-created owned file; not CLI execution credit."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from urllib.parse import quote, urlencode
from urllib.request import Request, ProxyHandler, build_opener
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bcs_e2e.file_assertions import FileExpectation
from bcs_e2e.legacy_file_assertions import LegacyFileExpectation
from bcs_e2e.local_adapter import OwnedLocalPilotAdapter, resolve_owned_profile
from bcs_e2e.local_fixtures import OwnedLocalFixtures
from bcs_e2e.owned_bcs import OwnedBcsProcess
from bcs_e2e.pilot import run_case
from bcs_e2e.prepare import prepare_run
from bcs_e2e.run_state import read
from bcs_e2e.transport import NoRedirect

BINARY=os.environ.get('BCS_E2E_TEST_BINARY','')
DIGEST=os.environ.get('BCS_E2E_TEST_BINARY_SHA256','')


@unittest.skipUnless(BINARY and DIGEST and shutil.which('lsof'),'approved actual BCS binary required')
class LegacyFileViewsLiveTests(unittest.TestCase):
    def test_native_metadata_capabilities_and_anonymous_views_match_owned_file_without_changing_pilot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';observed=[]
            with patch('bcs_e2e.pool.pool_root',return_value=root/'pools'):
                with OwnedBcsProcess(binary=Path(BINARY),binary_sha256=DIGEST,root=root/'runtime',run_id='legacy-file-views') as runtime:
                    OwnedLocalFixtures(runtime).apply(allow_write=True)
                    profile=resolve_owned_profile(runtime)
                    prepare_run(profile,state,runtime.run_id)
                    opener=build_opener(ProxyHandler({}),NoRedirect())
                    def get(adapter,path,*,anonymous=False,raw=False):
                        adapter.local_auth.require_bound(profile)
                        headers={} if anonymous else adapter.local_auth.headers(profile)
                        request=Request(runtime.inputs.origin+path,method='GET',headers=headers)
                        if anonymous:self.assertEqual(request.header_items(),[])
                        with opener.open(request,timeout=5) as response:
                            self.assertEqual(response.status,200)
                            payload=response.read(65_537)
                        self.assertLessEqual(len(payload),65_536)
                        adapter.local_auth.require_bound(profile)
                        return payload if raw else json.loads(payload)
                    def factory(current,*,state_dir,case_id,**_kwargs):
                        adapter=OwnedLocalPilotAdapter(runtime,current,state_dir=state_dir,case_id=case_id)
                        complete=adapter.file_complete;share=adapter.file_share;upload=adapter.file_upload
                        expected=[];submitted=[]
                        def checked_upload(session_id,file_id,payload):
                            submitted.append(bytes(payload))
                            return upload(session_id,file_id,payload)
                        def checked_complete(session_id,file_id):
                            metadata,record=complete(session_id,file_id)
                            expectation=LegacyFileExpectation(FileExpectation(file_id,session_id,metadata['file_name'],
                                metadata['mime_type'],len(submitted[0]),hashlib.sha256(submitted[0]).hexdigest(),
                                'human_'+profile['human']['user_id'],'local'))
                            path='/sessions/'+quote(session_id,safe='')+'/files'
                            native=get(adapter,path+'/'+quote(file_id,safe=''))
                            self.assertTrue(expectation.matches_metadata(native))
                            self.assertEqual(native['owner']['actor_kind'],'Human')
                            self.assertEqual(native['status'],'Ready')
                            self.assertTrue(expectation.matches_fresh_page(get(adapter,path)))
                            self.assertTrue(expectation.matches_local_capabilities(get(adapter,path+'/capabilities'),max_size=4096))
                            content=get(adapter,path+'/'+quote(file_id,safe='')+'/content',raw=True)
                            # Even a null metadata hash cannot weaken the known-upload oracle.
                            self.assertEqual(content,submitted[0])
                            self.assertTrue(expectation.matches_bytes(content))
                            expected.append((expectation,content))
                            observed.extend(('metadata','page','capabilities','content'))
                            return metadata,record
                        def checked_share(session_id,file_id):
                            result,record=share(session_id,file_id)
                            expectation,content=expected[0]
                            token=result['share_token']
                            query=urlencode({'token':token})
                            meta=get(adapter,'/sessions/shared-file?'+query,anonymous=True)
                            self.assertTrue(expectation.matches_shared_metadata(meta))
                            self.assertNotIn('session_id',meta)
                            shared=get(adapter,'/sessions/shared-file/content?'+query,anonymous=True,raw=True)
                            self.assertEqual(shared,submitted[0])
                            self.assertTrue(expectation.matches_bytes(shared))
                            observed.extend(('anonymous-metadata','anonymous-content'))
                            return result,record
                        adapter.file_upload=checked_upload;adapter.file_complete=checked_complete;adapter.file_share=checked_share
                        return adapter
                    result=run_case(profile,state,runtime.run_id,'PRE-03',adapter_factory=factory)
                    self.assertEqual(result['status'],'PASS',json.dumps({'status':result['status'],'functional':result['functional'],'cleanup':result['cleanup']}))
                    self.assertEqual(observed,['metadata','page','capabilities','content','anonymous-metadata','anonymous-content'])
                    self.assertEqual(result['cleanup'],'PASS')
                self.assertEqual(runtime.child.poll(),0)
                self.assertEqual(read(root/'runtime'/'owned-bcs.json')['status'],'STOPPED')
