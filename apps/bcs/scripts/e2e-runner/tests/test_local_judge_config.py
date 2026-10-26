"""Owned Judge config binds one live sandbox without changing other defaults."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_judge_sandbox import OwnedLocalJudgeSandbox
from bcs_e2e.owned_bcs import OwnedBcsProcess,file_sha256
from bcs_e2e.runtime import RuntimeContractError


class JudgeConfigTests(unittest.TestCase):
    def test_default_stays_no_llm_and_explicit_live_sandbox_key_is_environment_only(self):
        with tempfile.TemporaryDirectory() as tmp,OwnedLocalJudgeSandbox('judge') as sandbox:
            root=Path(tmp)
            default=prepare_local_bcs_inputs(root/'default',run_id='default',port=45001)
            self.assertEqual(json.loads(default.config_path.read_text())['llm'],{'type':'none'})
            inputs=prepare_local_bcs_inputs(root/'judge',run_id='judge',port=45002,store_messages=True,judge_sandbox=sandbox)
            text=inputs.config_path.read_text();config=json.loads(text)
            self.assertEqual(config['llm']['type'],'openai_compatible')
            self.assertEqual(config['llm']['base_url'],sandbox.judge_base_url)
            self.assertEqual(config['llm']['api_key_env'],'BCS_E2E_LOCAL_JUDGE_KEY')
            self.assertNotIn('api_key',config['llm']);self.assertNotIn(sandbox.judge_api_key,text)
            self.assertEqual(inputs.environment['BCS_E2E_LOCAL_JUDGE_KEY'],sandbox.judge_api_key)
            self.assertNotIn('BCS_E2E_LOCAL_JUDGE_KEY',default.environment)
            self.assertEqual(config['eventing'],{'enabled':False,'dispatcher_enabled':False})
            self.assertEqual(config['security']['outbound_url'],{'block_private_networks':True,'allow_loopback':True})
            self.assertEqual(config['bcsfuse'],{'enabled':False})

    def test_unmanaged_foreign_closed_or_combined_sandbox_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as tmp,OwnedLocalJudgeSandbox('judge') as sandbox:
            root=Path(tmp)
            for label,changes in [('unmanaged',{'judge_sandbox':object()}),('foreign',{'run_id':'foreign'}),
                                  ('storage',{'store_messages':False}),('provider',{'provider_sink':object()}),('event',{'event_sink':object()})]:
                args=dict(root=root/label,run_id='judge',port=45001,store_messages=True,judge_sandbox=sandbox);args.update(changes)
                with self.subTest(label=label),self.assertRaises(RuntimeContractError):prepare_local_bcs_inputs(**args)
                self.assertFalse((root/label).exists())
            sandbox.close()
            with self.assertRaises(RuntimeContractError):
                prepare_local_bcs_inputs(root/'closed',run_id='judge',port=45001,store_messages=True,judge_sandbox=sandbox)
            self.assertFalse((root/'closed').exists())

    def test_process_rechecks_owner_before_spawn(self):
        with tempfile.TemporaryDirectory() as tmp,OwnedLocalJudgeSandbox('judge') as sandbox:
            root=Path(tmp);binary=root/'helper';binary.write_text('#!/bin/sh\nexit 0\n');binary.chmod(0o700)
            runtime=OwnedBcsProcess(binary=binary,binary_sha256=file_sha256(binary),root=root/'run',run_id='judge',
                                    store_messages=True,judge_sandbox=sandbox)
            self.assertIs(runtime.judge_sandbox,sandbox)
            sandbox.close()
            with patch('bcs_e2e.owned_bcs.subprocess.Popen') as spawn:
                with self.assertRaises(RuntimeContractError):runtime.start()
                spawn.assert_not_called()
            self.assertFalse((root/'run').exists())


class JudgeClosureTests(unittest.TestCase):
    def test_close_interrupts_a_confirmed_partial_body_reader_without_approval(self):
        import socket
        import threading
        import time
        from urllib.parse import urlsplit
        with OwnedLocalJudgeSandbox('partial-judge') as sandbox:
            entered=threading.Event();original=sandbox._read_body
            def read_body(handler,length):
                entered.set();return original(handler,length)
            target=urlsplit(sandbox.judge_base_url+'/chat/completions')
            client=socket.create_connection((target.hostname,target.port),timeout=2)
            self.addCleanup(client.close)
            headers=(f'POST {target.path} HTTP/1.1\r\nHost: {target.hostname}:{target.port}\r\n'
                     f'Authorization: Bearer {sandbox.judge_api_key}\r\n'
                     'Content-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{')
            with patch.object(sandbox,'_read_body',side_effect=read_body):
                client.sendall(headers.encode());self.assertTrue(entered.wait(1))
                started=time.monotonic();sandbox.close();elapsed=time.monotonic()-started
            self.assertLess(elapsed,3)
            snapshot=sandbox.snapshot()
            self.assertEqual(snapshot['state'],'CLOSED')
            self.assertFalse(snapshot['judge_released']);self.assertFalse(snapshot['judge_finished'])
            self.assertIsNone(sandbox._judge_api_key);self.assertIsNone(sandbox._provider_token)
