import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import copy
from datetime import datetime
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from journal import Journal, read
from lark_capture import fetch, collect, CaptureError
from scheduled_capture import due_dates

SETTINGS = dict(chat_id='fictional-chat', sender_open_bot_id='fictional-bot', primary_marker='[TEST_SUMMARY]', cli_path='/fictional/lark-cli', timezone='Asia/Shanghai')

def payload():
    return {'ok':True,'data':{'has_more':False,'messages':[{'chat_id':'fictional-chat','sender':{'open_bot_id':'fictional-bot'},'content':'[TEST_SUMMARY] 2026-10-16\nFictional completed task','create_time':'2026-10-16 19:24','message_id':'fictional-message','msg_type':'interactive'}]},'meta':{'pagination':{'complete':True}}}

def adapter(value):
    return lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')

class RealContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.j=Journal(Path(self.temp.name)/'data'); self.j.init()
    def result(self, value=None):
        return fetch(SETTINGS, '2026-10-16', run=adapter(value or payload()))
    def test_actual_shape_naive_time_interactive(self):
        self.assertEqual(len(self.result()['messages']),1)
    def test_scope(self):
        p=payload(); p['data']['messages'][0]['chat_id']='other'
        self.assertEqual(self.result(p)['messages'],[])
    def test_sender(self):
        p=payload();p['data']['messages'][0]['sender']=None
        self.assertEqual(self.result(p)['messages'],[])
    def test_incomplete(self):
        p=payload();p['meta']['pagination']['complete']=False
        with self.assertRaises(CaptureError): self.result(p)
    def test_bad_envelope(self):
        p=payload();p['data']=None
        with self.assertRaises(CaptureError):self.result(p)
    def test_wrong_day(self):
        p=payload();p['data']['messages'][0]['create_time']='2026-10-17 00:00'
        self.assertEqual(self.result(p)['messages'],[])
    def test_primary_priority(self):
        p=payload();m=copy.deepcopy(p['data']['messages'][0]);m.update(message_id='fallback',content='每日回顾');p['data']['messages'].append(m)
        self.assertEqual(len(self.result(p)['messages']),1)
    def test_redaction(self):
        p=payload();p['data']['messages'][0]['content']+=' password=fictional-secret'
        self.assertNotIn('fictional-secret',self.result(p)['messages'][0]['text'])
    def test_repeat_and_edit(self):
        r=self.result();f=lambda *a:r
        self.assertEqual(collect(self.j,SETTINGS,'2026-10-16',f)['status'],'saved')
        self.assertEqual(collect(self.j,SETTINGS,'2026-10-16',f)['status'],'unchanged')
        self.assertEqual(len(self.j.records('2026-10-16')),1)
        r['messages'][0]['version']='changed'
        self.assertEqual(collect(self.j,SETTINGS,'2026-10-16',f)['status'],'source_changed_needs_review')
    def test_failure_no_commit(self):
        def fail(*a):raise CaptureError('authorization_required')
        self.assertFalse(collect(self.j,SETTINGS,'2026-10-16',fail)['committed'])
        self.assertFalse((self.j.root/'capture-state/lark.json').exists())
    def test_save_failure_retry(self):
        f=lambda *a:self.result()
        with patch('lark_capture.atomic',side_effect=OSError):
            self.assertEqual(collect(self.j,SETTINGS,'2026-10-16',f)['status'],'save_failed')
        self.assertEqual(collect(self.j,SETTINGS,'2026-10-16',f)['status'],'saved')
        self.assertEqual(len(self.j.records('2026-10-16')),1)
    def test_final_protected(self):
        self.j.final_dir('2026-10-16').mkdir(parents=True)
        def forbidden(*a):raise AssertionError('must not fetch')
        self.assertEqual(collect(self.j,SETTINGS,'2026-10-16',forbidden)['status'],'final_protected')
    def test_evening_and_expiry(self):
        self.assertEqual(due_dates(datetime.fromisoformat('2026-10-18T22:30:00+08:00'),'2026-10-18','2026-10-18'),['2026-10-18'])
        self.assertEqual(due_dates(datetime.fromisoformat('2026-10-19T08:00:00+08:00'),'2026-10-18','2026-10-18'),['2026-10-18'])
        self.assertEqual(due_dates(datetime.fromisoformat('2026-10-20T08:00:00+08:00'),'2026-10-18','2026-10-18'),[])
    def test_before_deadline(self):
        self.assertEqual(due_dates(datetime.fromisoformat('2026-10-18T22:29:00+08:00'),'2026-10-18','2026-10-18'),[])
