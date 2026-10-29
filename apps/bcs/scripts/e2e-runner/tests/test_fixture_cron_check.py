"""No real HTTP or credentials; exercise the new CLI composition and finite GET set."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.fixture_cron_check import fixture_cron_check
from bcs_e2e.profile import ConfigError
from bcs_e2e.cli import main
from bcs_e2e.run_state import write
import test_runner as fixtures


class Response:
    def __init__(self, body, *, code=200, media='application/json'):
        self.code=code;self.headers={'Content-Type':media}
        self.body=json.dumps(body).encode() if not isinstance(body,bytes) else body
        self.closed=False
    def read(self,limit):return self.body[:limit]
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True


class Client:
    def __init__(self, responses):
        self.responses=list(responses);self.headers={'Cookie':'PRIVATE-COOKIE'}
        self.evidence=[];self.calls=[];self.opener=self
    def open(self,request,**kwargs):
        self.calls.append((request,kwargs))
        response=self.responses.pop(0)
        if isinstance(response,BaseException):raise response
        return response


class FixtureCronCheckTests(unittest.TestCase):
    def setUp(self):
        self.profile=fixtures.profile()
        self.profile['browser_origin']='https://browser.example.test'
        self.profile['auth']={'header_env':{'Cookie':'TEST_PRIVATE_COOKIE'}}
        self.profile['actors']['driver']['bot_id']='backend-driver:'+self.profile['human']['user_id']
        self.profile['actors']['member']['bot_id']='backend-member:'+self.profile['human']['user_id']

    def response(self):return Response({'success':True,'error_code':200,'data':[],'failed_targets':[]})

    def run_check(self, responses=None, *, preflight='DISCOVERY_PASSED'):
        client=Client(responses if responses is not None else [self.response(),self.response()])
        with patch('bcs_e2e.fixture_cron_check.ReadClient',return_value=client), \
             patch('bcs_e2e.fixture_cron_check.check',return_value={'status':preflight}):
            report=fixture_cron_check(self.profile,allow_read=True,confirm_human_id=self.profile['human']['user_id'],confirm_frontend_origin=self.profile['browser_origin'])
        return report,client

    def test_two_exact_frontend_gets_never_owner_wide_or_cron_trigger(self):
        report,client=self.run_check()
        self.assertEqual(report['status'],'OBSERVED')
        self.assertEqual(len(client.calls),2)
        for (request,kwargs),role in zip(client.calls,('driver','member')):
            self.assertEqual(request.get_method(),'GET')
            self.assertIsNone(request.data)
            self.assertTrue(request.full_url.startswith('https://browser.example.test/api/cron?'))
            self.assertIn('bot_id=backend-'+role,request.full_url)
            self.assertNotIn('all',request.full_url)
            self.assertNotIn('/run',request.full_url)
            self.assertEqual(request.get_header('Cookie'),'PRIVATE-COOKIE')
            self.assertEqual(kwargs['timeout'],self.profile['policy']['timeout_seconds'])
        self.assertNotIn('PRIVATE-COOKIE',json.dumps(report))
        self.assertFalse(report['ready_to_execute']);self.assertFalse(report['pool_release_authorized'])
        self.assertEqual(report['input_boundary'],'NOT_PROVEN')

    def test_missing_permission_wrong_human_or_unmapped_actor_never_resolves_credentials(self):
        with patch('bcs_e2e.fixture_cron_check.ReadClient',side_effect=AssertionError('no credentials')):
            for allow,human in ((False,self.profile['human']['user_id']),(True,'foreign')):
                with self.assertRaises(ConfigError):fixture_cron_check(self.profile,allow_read=allow,confirm_human_id=human,confirm_frontend_origin=self.profile['browser_origin'])
            self.profile['actors']['driver']['bot_id']='provider-id'
            with self.assertRaises(ConfigError):fixture_cron_check(self.profile,allow_read=True,confirm_human_id=self.profile['human']['user_id'],confirm_frontend_origin=self.profile['browser_origin'])

    def test_failed_gateway_preflight_makes_no_frontend_call(self):
        report,client=self.run_check(preflight='BLOCKED')
        self.assertEqual(report['status'],'BLOCKED');self.assertEqual(client.calls,[])

    def test_html_200_is_blocked_not_empty_list_or_cookie_failure(self):
        html=Response(b'<html>PRIVATE-PAGE</html>',media='text/html')
        report,client=self.run_check([html])
        self.assertEqual(len(client.calls),1)
        self.assertEqual(report['status'],'BLOCKED');self.assertEqual(report['actors'],{})
        self.assertEqual(report['requests'][0]['reason'],'EXPECTED_JSON_NOT_PAGE_CONTENT')
        self.assertTrue(html.closed);self.assertNotIn('PRIVATE',json.dumps(report))

    def test_redirect_auth_or_http_error_never_retries_or_calls_second_bot(self):
        for code in (302,401,403,404,503):
            with self.subTest(code=code):
                report,client=self.run_check([Response({},code=code)])
                self.assertEqual(len(client.calls),1);self.assertEqual(report['status'],'BLOCKED')
                self.assertEqual(report['requests'][0]['status'],'HTTP_REJECTED')
                self.assertFalse(report['route_fallback'])

    def test_partial_runtime_failure_survives_and_does_not_stop_independent_second_read(self):
        response=Response({'success':True,'error_code':200,'data':[],
            'failed_targets':[{'bot_id':'backend-driver','owner_id':self.profile['human']['user_id'],
                               'runtime_stage':'verify','message':'PRIVATE-ERROR'}]})
        report,client=self.run_check([response,self.response()])
        self.assertEqual(report['status'],'INCOMPLETE');self.assertEqual(len(client.calls),2)
        self.assertNotIn('PRIVATE',json.dumps(report))

    def test_transport_schema_byte_budget_and_deep_json_fail_closed(self):
        p=copy.deepcopy(self.profile)
        self.profile['policy']['max_response_bytes']=1024
        for response in (TimeoutError('PRIVATE'),Response(b'x'*1025),Response(b'not JSON'),
                         Response({'code':0,'data':{'items':[]}}),Response({'success':True,'data':[]})):
            report,client=self.run_check([response])
            self.assertEqual(report['status'],'BLOCKED');self.assertEqual(len(client.calls),1)
            self.assertNotIn('PRIVATE',json.dumps(report))
        self.profile=p
        report,_=self.run_check([Response(b'['*1500+b'0'+b']'*1500)])
        self.assertEqual(report['status'],'BLOCKED')

    def test_keyboard_interrupt_preserves_partial_read_evidence(self):
        report,client=self.run_check([KeyboardInterrupt()])
        self.assertEqual(report['status'],'CANCELLED');self.assertEqual(len(client.calls),1)
        self.assertEqual(report['requests'][0]['status'],'CANCELLED')

    def test_error_response_does_not_read_next_actor(self):
        report,client=self.run_check([Response({'success':False,'error_code':500,'data':[],
            'failed_targets':[],'message':'PRIVATE'})])
        self.assertEqual(report['status'],'BLOCKED');self.assertEqual(len(client.calls),1)
        self.assertEqual(report['actors']['driver']['status'],'BLOCKED')

    def test_cli_composition_is_explicit_and_never_qualifies(self):
        with tempfile.TemporaryDirectory() as directory:
            profile=Path(directory)/'profile.json';write(profile,self.profile)
            output=io.StringIO();client=Client([self.response(),self.response()])
            with patch('bcs_e2e.fixture_cron_check.ReadClient',return_value=client), \
                 patch('bcs_e2e.fixture_cron_check.check',return_value={'status':'DISCOVERY_PASSED'}), \
                 contextlib.redirect_stdout(output):
                rc=main(['fixture-cron-check','--profile',str(profile),'--allow-read',
                         '--confirm-human-id',self.profile['human']['user_id'],
                         '--confirm-frontend-origin',self.profile['browser_origin']])
            self.assertEqual(rc,0,output.getvalue())
            report=json.loads(output.getvalue());self.assertFalse(report['ready_to_execute'])
            self.assertEqual(report['status'],'OBSERVED')

    def test_browser_header_origin_is_not_implicit_cookie_destination_authority(self):
        with patch('bcs_e2e.fixture_cron_check.ReadClient',side_effect=AssertionError('no credential resolution')):
            with self.assertRaises(ConfigError):
                fixture_cron_check(self.profile,allow_read=True,confirm_human_id=self.profile['human']['user_id'],
                                   confirm_frontend_origin='https://another.example.test')

    def test_http_error_exception_is_closed_without_error_body_or_fallback(self):
        response=HTTPError('https://browser.example.test',403,'PRIVATE-ERROR',{},io.BytesIO(b'PRIVATE-BODY'))
        report,client=self.run_check([response])
        self.assertEqual(len(client.calls),1);self.assertEqual(report['requests'][0]['http_status'],403)
        self.assertNotIn('PRIVATE',json.dumps(report));self.assertEqual(report['status'],'BLOCKED')

    def test_preflight_cancellation_does_not_query_frontend(self):
        client=Client([])
        with patch('bcs_e2e.fixture_cron_check.ReadClient',return_value=client), \
             patch('bcs_e2e.fixture_cron_check.check',side_effect=KeyboardInterrupt()):
            result=fixture_cron_check(self.profile,allow_read=True,confirm_human_id=self.profile['human']['user_id'],
                                      confirm_frontend_origin=self.profile['browser_origin'])
        self.assertEqual(result['status'],'CANCELLED');self.assertEqual(client.calls,[])

    def test_all_reserved_selector_is_rejected_before_credentials(self):
        for name in ('all','ALL'):
            self.profile['actors']['driver']['bot_id']=name+':'+self.profile['human']['user_id']
            with patch('bcs_e2e.fixture_cron_check.ReadClient',side_effect=AssertionError('no credentials')):
                with self.assertRaises(ConfigError):
                    fixture_cron_check(self.profile,allow_read=True,confirm_human_id=self.profile['human']['user_id'],
                                       confirm_frontend_origin=self.profile['browser_origin'])

    def test_second_target_business_failure_remains_blocked(self):
        bad=Response({'success':False,'error_code':500,'data':[],'failed_targets':[]})
        report,client=self.run_check([self.response(),bad])
        self.assertEqual(len(client.calls),2)
        self.assertEqual(report['actors']['member']['status'],'BLOCKED')
        self.assertEqual(report['status'],'BLOCKED')

    def test_actual_preflight_parser_never_echoes_response_id_text(self):
        import os
        from bcs_e2e.identity_binding import HUMAN_VIEW
        from bcs_e2e.transport import ReadClient
        self.profile['identity_binding']=dict(HUMAN_VIEW)
        owner=self.profile['human']['user_id']
        values=[{'env':'pre'},{'bot_id':'human_'+owner,'kind':'human','created_by':owner,'env':'pre'},
                {'items':[],'total':0,'offset':0,'limit':1}]
        values.extend({'bot_id':self.profile['actors'][role]['bot_id'],'kind':'bot','created_by':owner,
                       'env':'pre','status':'online'} for role in ('driver','member'))
        responses=[Response({'code':20000,'message':'OK','request_id':'PRIVATE-SERVER-TEXT', 'data':v}) for v in values]
        for cancelled in (False,True):
            opener=Client(responses[:1]+[KeyboardInterrupt()] if cancelled else responses+[self.response(),self.response()])
            with patch.dict(os.environ,{'TEST_PRIVATE_COOKIE':'PRIVATE-COOKIE'}), \
                 patch('bcs_e2e.transport.build_opener',return_value=opener):
                result=fixture_cron_check(self.profile,allow_read=True,confirm_human_id=owner,
                                          confirm_frontend_origin=self.profile['browser_origin'])
            self.assertEqual(result['status'],'CANCELLED' if cancelled else 'OBSERVED',result)
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertEqual(len(result['preflight']['requests']),2 if cancelled else 5)
            self.assertIn('response_request_id_sha256',result['preflight']['requests'][0])


if __name__=='__main__':unittest.main()
