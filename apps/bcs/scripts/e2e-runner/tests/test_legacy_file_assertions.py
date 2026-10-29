"""Native DTO, Bot ownership and anonymous privacy cannot alias the V1 face."""
import copy
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.file_assertions import FileExpectation
from bcs_e2e.legacy_file_assertions import LegacyFileExpectation


class LegacyFileAssertionTests(unittest.TestCase):
    def setUp(self):
        self.content=b'cli-file-story\n'
        self.semantic=FileExpectation('f1','g1:12345678','sample.txt','text/plain',len(self.content),
                                      hashlib.sha256(self.content).hexdigest(),'bot-pm','local',owner_kind='bot')
        self.expected=LegacyFileExpectation(self.semantic)
        self.metadata={'file_id':'f1','session_id':'g1:12345678','file_name':'sample.txt','mime_type':'text/plain',
                       'size':len(self.content),'sha256':self.semantic.sha256,'owner':{'actor_kind':'Bot','actor_id':'bot-pm'},
                       'storage_backend':'local','status':'Ready','created_at':1,'updated_at':2}
        self.shared={key:value for key,value in self.metadata.items() if key!='session_id'}
        self.capabilities={'storage':'local','presign_upload':False,'presign_download':False,'inline_view':True,'max_size':4096}

    def test_bot_owner_matches_only_explicit_bot_expectation_and_normalized_face(self):
        self.assertTrue(self.expected.matches_metadata(self.metadata))
        self.assertFalse(LegacyFileExpectation(replace(self.semantic,owner_kind='human')).matches_metadata(self.metadata))
        self.assertFalse(self.semantic.matches_metadata(self.metadata))
        normalized={**self.metadata,'status':'ready','owner':{'actor_kind':'bot','actor_id':'bot-pm'}}
        self.assertTrue(self.semantic.matches_metadata(normalized))
        self.assertFalse(self.expected.matches_metadata(normalized))
        with self.assertRaises(ValueError):replace(self.semantic,owner_kind='Bot')

    def test_existing_human_default_is_preserved_without_automatic_owner_guessing(self):
        expected=replace(self.semantic,owner_id='human_local',owner_kind='human')
        value={**self.metadata,'owner':{'actor_kind':'Human','actor_id':'human_local'}}
        self.assertTrue(LegacyFileExpectation(expected).matches_metadata(value))
        default=FileExpectation('f1','g1:12345678','sample.txt','text/plain',len(self.content),self.semantic.sha256,'human_local','local')
        self.assertEqual(default.owner_kind,'human')
        self.assertFalse(LegacyFileExpectation(default).matches_metadata(self.metadata))

    def test_anonymous_metadata_forbids_session_and_internal_fields_even_when_null(self):
        original=copy.deepcopy(self.shared)
        self.assertTrue(self.expected.matches_shared_metadata(self.shared))
        for key in ('session_id','object_handle','upload_url','share_token','download_url','headers'):
            for value in (None,'private'):
                with self.subTest(key=key,value=value):
                    self.assertFalse(self.expected.matches_shared_metadata({**self.shared,key:value}))
        self.assertFalse(self.expected.matches_shared_metadata(self.metadata))
        self.assertFalse(self.expected.matches_metadata(self.shared))
        self.assertEqual(self.shared,original)

    def test_metadata_identity_type_hash_and_enum_faults_fail_closed(self):
        changes=[('file_id','other'),('session_id','other'),('file_name','other'),('mime_type','other'),
                 ('size',True),('size',str(len(self.content))),('sha256','bad'),('owner',{'actor_kind':'Bot','actor_id':'other'}),
                 ('owner',{'actor_kind':'Human','actor_id':'bot-pm'}),('owner',{'actor_kind':[],'actor_id':'bot-pm'}),
                 ('owner',[]),('owner',{'actor_kind':'Bot','actor_id':'bot-pm','token':'not-allowed'}),
                 ('storage_backend','baas'),('status','ready'),('status','Pending'),('updated_at',True),('created_at',-1)]
        for key,value in changes:
            with self.subTest(key=key,value=value):
                self.assertFalse(self.expected.matches_metadata({**self.metadata,key:value}))
                if key!='session_id':self.assertFalse(self.expected.matches_shared_metadata({**self.shared,key:value}))
        for value in (None,[],True,{'success':True,'data':self.metadata}):
            self.assertFalse(self.expected.matches_metadata(value))
            self.assertFalse(self.expected.matches_shared_metadata(value))
        for key in self.metadata:
            self.assertFalse(self.expected.matches_metadata({k:v for k,v in self.metadata.items() if k!=key}))
        self.assertFalse(self.expected.matches_metadata({**self.metadata,'object_handle':'private'}))

    def test_nullable_hash_is_not_a_substitute_for_byte_roundtrip(self):
        self.assertTrue(self.expected.matches_metadata({**self.metadata,'sha256':None}))
        self.assertTrue(self.expected.matches_shared_metadata({**self.shared,'sha256':None}))
        self.assertTrue(self.expected.matches_bytes(self.content))
        for value in (self.content.decode(),self.content+b'!',b'x'*len(self.content),None):
            self.assertFalse(self.expected.matches_bytes(value))

    def test_fresh_page_does_not_accept_truncated_duplicate_or_unrelated_inventory(self):
        self.assertTrue(self.expected.matches_fresh_page({'items':[self.metadata],'total':1}))
        for value in ({'items':[self.metadata],'total':True},{'items':[self.metadata],'total':2},
                      {'items':[self.metadata]*2,'total':2},{'items':[],'total':1},
                      {'items':self.metadata,'total':1},{'items':[self.metadata],'total':1,'next':'more'},
                      {'items':[{**self.metadata,'file_id':'other'}],'total':1}):
            self.assertFalse(self.expected.matches_fresh_page(value))

    def test_local_capabilities_require_declared_backend_and_strict_booleans(self):
        self.assertTrue(self.expected.matches_local_capabilities(self.capabilities,max_size=4096))
        for key,value in (('storage','baas'),('presign_upload',0),('presign_download',0),('inline_view',1),('max_size',True),('max_size',4095)):
            self.assertFalse(self.expected.matches_local_capabilities({**self.capabilities,key:value},max_size=4096))
        self.assertFalse(self.expected.matches_local_capabilities(self.capabilities,max_size=True))
        self.assertFalse(LegacyFileExpectation(replace(self.semantic,storage_backend='baas')).matches_local_capabilities(self.capabilities,max_size=4096))
