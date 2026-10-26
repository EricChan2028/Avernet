"""Opt-in owned Eventing config must not relax the shared outbound policy."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_config import prepare_local_bcs_inputs
from bcs_e2e.local_event_sink import OwnedLocalEventSink
from bcs_e2e.local_provider_sink import OwnedLocalProviderSink
from bcs_e2e.owned_bcs import OwnedBcsProcess,file_sha256
from bcs_e2e.runtime import RuntimeContractError


class EventConfigTests(unittest.TestCase):
    def test_default_remains_disabled_and_live_input_only_enables_eventing_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            default=prepare_local_bcs_inputs(root/'default',run_id='default',port=45001)
            self.assertEqual(json.loads(default.config_path.read_text())['eventing'],{'enabled':False,'dispatcher_enabled':False})
            with OwnedLocalEventSink('events') as sink:
                config=prepare_local_bcs_inputs(root/'event',run_id='events',port=45002,store_messages=True,event_sink=sink)
                value=json.loads(config.config_path.read_text())
                self.assertTrue(value['eventing']['enabled']);self.assertTrue(value['eventing']['dispatcher_enabled'])
                self.assertEqual(value['eventing']['worker_concurrency'],1)
                self.assertEqual(value['eventing']['per_host_concurrency'],1)
                self.assertEqual(value['eventing']['webhook']['private_endpoint_allowlist'],[])
                self.assertTrue(value['eventing']['webhook']['block_private_networks'])
                self.assertTrue(value['eventing']['webhook']['allow_http_loopback'])
                self.assertTrue(value['eventing']['webhook']['allow_non_standard_ports'])
                self.assertEqual(value['security']['outbound_url'],{'block_private_networks':True,'allow_loopback':False})

    def test_wrong_run_unmanaged_closed_or_mixed_receivers_are_rejected_before_files(self):
        with tempfile.TemporaryDirectory() as tmp,OwnedLocalEventSink('events') as sink:
            root=Path(tmp)
            for label,changes in [('unmanaged',{'event_sink':object()}),('wrong-run',{'run_id':'wrong'}),
                                  ('no-storage',{'store_messages':False}),('provider',{'provider_sink':object()})]:
                args=dict(root=root/label,run_id='events',port=45001,store_messages=True,event_sink=sink);args.update(changes)
                with self.subTest(label=label),self.assertRaises(RuntimeContractError):prepare_local_bcs_inputs(**args)
                self.assertFalse((root/label).exists())
            sink.close()
            with self.assertRaises(RuntimeContractError):
                prepare_local_bcs_inputs(root/'closed',run_id='events',port=45001,store_messages=True,event_sink=sink)
            self.assertFalse((root/'closed').exists())

    def test_process_rechecks_live_handle_before_spawn_and_does_not_stop_receiver_owner(self):
        with tempfile.TemporaryDirectory() as tmp,OwnedLocalEventSink('events') as sink:
            root=Path(tmp);binary=root/'helper';binary.write_text('#!/bin/sh\nexit 0\n');binary.chmod(0o700)
            runtime=OwnedBcsProcess(binary=binary,binary_sha256=file_sha256(binary),root=root/'runtime',
                                    run_id='events',store_messages=True,event_sink=sink)
            self.assertIs(runtime.event_sink,sink)
            sink.close()
            with patch('bcs_e2e.owned_bcs.subprocess.Popen') as spawn:
                with self.assertRaises(RuntimeContractError):runtime.start()
                spawn.assert_not_called()
            self.assertFalse((root/'runtime').exists())
