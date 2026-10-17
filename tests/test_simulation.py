import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import journal
import simulate as sim


def message(**overrides):
    item = {'id':'fictional-message-1','chat':'fixture-chat','sender':'fixture-bot',
            'summary_date':'2026-10-17','type':'daily_summary','marker':sim.MARKER,
            'title':'工作日结','created_at':'2026-10-17T22:20:00+08:00',
            'updated_at':'2026-10-17T22:20:00+08:00',
            'items':[{'kind':'event','text':'虚构：完成了展板文字校对。'},
                     {'kind':'plan','text':'虚构：明天准备修改配图。'}]}
    item.update(overrides)
    return item


def source(*messages, status='ok'):
    return sim.FixtureSource({'simulation_only':True,'pages':[{'status':status,'messages':list(messages)}]})


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.j = journal.Journal(Path(self.temp.name)/'mock')
        self.j.init(demo=True)
        self.collector = sim.SimulatedCollector(self.j)
        # A test failure must expose any accidental real network attempt.
        self.network = patch('socket.socket', side_effect=AssertionError('network forbidden'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.temp.cleanup()

    def collect(self, *items):
        return self.collector.run('2026-10-17',source(*items))

    def test_happy_path_draft_only_and_plan_preserved(self):
        result=self.collect(message())
        self.assertEqual(result['status'],'saved')
        self.assertEqual([r['kind'] for r in self.j.records('2026-10-17')],['event','plan'])
        self.assertEqual(self.j.entries(),[])
        self.assertTrue((self.j.root/'drafts/2026-10-17.json').exists())

    def test_repeated_scan_no_duplicate(self):
        self.collect(message());before=self.collector.path.read_bytes()
        self.assertEqual(self.collect(message())['status'],'unchanged')
        self.assertEqual(len(self.j.records('2026-10-17')),2)
        self.assertEqual(before,self.collector.path.read_bytes())

    def test_restrict_chat_sender_date_and_type(self):
        result=self.collect(message(chat='other'),message(sender='other'),
                            message(summary_date='2026-10-16'),message(type='morning_preview'))
        self.assertEqual(result['status'],'missing_summary')
        self.assertFalse(self.collector.path.exists())

    def test_marked_summary_preferred_over_fallback(self):
        self.collect(message(),message(id='fallback',marker='',title='每日回顾'))
        self.assertEqual(len(self.j.records('2026-10-17')),2)
        self.assertEqual(next(iter(journal.read(self.collector.path).values()))['count'],1)

    def test_fallback_without_marker(self):
        self.assertEqual(self.collect(message(marker='',title='每日回顾'))['status'],'saved')

    def test_missing_summary_no_fake_diary(self):
        self.assertEqual(self.collect()['status'],'missing_summary')
        self.assertEqual(self.j.records('2026-10-17'),[])

    def test_failures_do_not_advance_capture(self):
        for code in ['timeout','unauthorized','rate_limited']:
            result=self.collector.run('2026-10-17',source(status=code))
            self.assertEqual(result['status'],code)
            self.assertFalse(self.collector.path.exists())

    def test_later_page_failure_does_not_ingest_partial_results(self):
        pages=[{'status':'ok','messages':[message()]},{'status':'timeout','messages':[]}]
        result=self.collector.run('2026-10-17',sim.FixtureSource({'simulation_only':True,'pages':pages}))
        self.assertEqual(result['status'],'timeout')
        self.assertEqual(self.j.records('2026-10-17'),[])

    def test_pagination_duplicate_keeps_latest_uncommitted_version(self):
        changed=message(updated_at='2026-10-17T22:25:00+08:00',items=[{'kind':'event','text':'虚构：只完成一半校对。'}])
        self.collect(message(),message(),changed)
        self.assertEqual(len(self.j.records('2026-10-17')),1)
        self.assertIn('一半',self.j.records('2026-10-17')[0]['text'])

    def test_edited_committed_summary_requires_review(self):
        self.collect(message());before=self.collector.path.read_bytes()
        result=self.collect(message(updated_at='2026-10-17T23:00:00+08:00',items=[{'kind':'event','text':'虚构：修改后的总结。'}]))
        self.assertEqual(result['status'],'edited_summary_needs_review')
        self.assertEqual(self.collector.path.read_bytes(),before)
        self.assertEqual(len(self.j.records('2026-10-17')),2)

    def test_fallback_replaced_by_marker_requires_review(self):
        self.collect(message(id='fallback',marker='',title='每日回顾'))
        self.assertEqual(self.collect(message())['status'],'selection_changed_needs_review')

    def test_draft_write_failure_then_retry_is_idempotent(self):
        with patch.object(self.j,'draft',side_effect=OSError('disk failure')):
            self.assertEqual(self.collect(message())['status'],'save_failed')
        self.assertFalse(self.collector.path.exists())
        self.assertEqual(self.collect(message())['status'],'saved')
        self.assertEqual(len(self.j.records('2026-10-17')),2)

    def test_edit_after_failed_save_does_not_append_contradiction(self):
        with patch.object(self.j,'draft',side_effect=OSError()):self.collect(message())
        changed=message(items=[{'kind':'event','text':'虚构：改口为未完成。'}])
        self.assertEqual(self.collect(changed)['status'],'edited_summary_needs_review')
        self.assertFalse(self.collector.path.exists())

    def test_commit_failure_then_retry(self):
        with patch.object(sim,'atomic',side_effect=OSError()):
            self.assertEqual(self.collect(message())['status'],'save_failed')
        self.assertFalse(self.collector.path.exists())
        self.assertEqual(self.collect(message())['status'],'saved')
        self.assertEqual(len(self.j.records('2026-10-17')),2)

    def test_post_midnight_summary_uses_explicit_target(self):
        self.collect(message(created_at='2026-10-18T00:10:00+08:00',updated_at='2026-10-18T00:10:00+08:00'))
        self.assertEqual(len(self.j.records('2026-10-17')),2)
        self.assertEqual(self.j.records('2026-10-18'),[])

    def test_final_is_untouched(self):
        self.collect(message());dest=self.j.finalize('2026-10-17',True);before=(dest/'record.json').read_bytes()
        self.assertEqual(self.collect(message(id='new'))['status'],'selection_changed_needs_review')
        self.assertEqual(self.collect(message())['status'],'final_protected')
        self.assertEqual((dest/'record.json').read_bytes(),before)

    def test_secondary_source_cannot_infer_feelings(self):
        result=self.collect(message(items=[{'kind':'feeling','text':'猜测开心'}]))
        self.assertEqual(result['status'],'invalid_payload')
        self.assertFalse(self.collector.path.exists())

    def test_state_has_no_raw_body_or_message_id(self):
        self.collect(message());state=self.collector.path.read_text()
        self.assertNotIn('展板',state);self.assertNotIn('fictional-message',state);self.assertNotIn('fixture-chat',state)

    def test_simulation_refuses_real_data_directory_and_unmarked_input(self):
        with self.assertRaises(ValueError):sim.FixtureSource({'pages':[]})
        real=journal.Journal(Path(self.temp.name)/'non-demo');real.init()
        with self.assertRaises(ValueError):sim.SimulatedCollector(real).run('2026-10-17',source(message()))

    def test_before_due_and_exact_due_using_configured_timezone(self):
        schedule=sim.SimulatedSchedule(self.j,'2026-10-17')
        calls=[]
        def run(d):calls.append(d);return self.collect(message())
        self.assertEqual(schedule.tick(sim.aware('2026-10-17T14:29:00+00:00'),run)['runs'],[])
        self.assertEqual(len(schedule.tick(sim.aware('2026-10-17T14:30:00+00:00'),run)['runs']),1)
        self.assertEqual(schedule.tick(sim.aware('2026-10-17T23:00:00+08:00'),run)['runs'],[])
        self.assertEqual(calls,['2026-10-17'])

    def test_missing_then_retry_after_interval(self):
        schedule=sim.SimulatedSchedule(self.j,'2026-10-17')
        result=schedule.tick(sim.aware('2026-10-17T22:30:00+08:00'),lambda d:self.collect())
        self.assertEqual(result['runs'][0]['status'],'missing_summary')
        self.assertEqual(schedule.tick(sim.aware('2026-10-17T22:45:00+08:00'),lambda d:self.collect(message()))['runs'],[])
        result=schedule.tick(sim.aware('2026-10-17T23:00:00+08:00'),lambda d:self.collect(message()))
        self.assertEqual(result['runs'][0]['status'],'saved')

    def test_wake_after_midnight_backfills_yesterday_not_today(self):
        schedule=sim.SimulatedSchedule(self.j,'2026-10-17');calls=[]
        def run(d):calls.append(d);return {'date':d,'status':'saved'}
        schedule.tick(sim.aware('2026-10-18T08:00:00+08:00'),run)
        self.assertEqual(calls,['2026-10-17'])

    def test_long_sleep_has_bounded_backfill_and_explicit_gap(self):
        schedule=sim.SimulatedSchedule(self.j,'2026-10-01');calls=[]
        def run(d):calls.append(d);return {'date':d,'status':'saved'}
        result=schedule.tick(sim.aware('2026-10-17T22:30:00+08:00'),run)
        self.assertEqual(calls,['2026-10-14','2026-10-15','2026-10-16','2026-10-17'])
        self.assertEqual(result['outside_window'],{'from':'2026-10-01','through':'2026-10-13'})

    def test_schedule_save_failure_retries_without_duplicate_diary(self):
        schedule=sim.SimulatedSchedule(self.j,'2026-10-17');original=sim.atomic
        def fail(path,value):
            if Path(path).name=='schedule.json':raise OSError()
            return original(path,value)
        with patch.object(sim,'atomic',fail):
            with self.assertRaises(OSError):schedule.tick(sim.aware('2026-10-17T22:30:00+08:00'),lambda d:self.collect(message()))
        result=schedule.tick(sim.aware('2026-10-17T22:31:00+08:00'),lambda d:self.collect(message()))
        self.assertEqual(result['runs'][0]['status'],'unchanged')
        self.assertEqual(len(self.j.records('2026-10-17')),2)

    def test_manual_late_recheck_after_success(self):
        schedule=sim.SimulatedSchedule(self.j,'2026-10-17')
        schedule.tick(sim.aware('2026-10-17T22:30:00+08:00'),lambda d:self.collect(message()))
        result=self.collect(message(),message(id='second',items=[{'kind':'thread','text':'虚构：等待确认配图。'}]))
        self.assertEqual(result['status'],'saved')
        self.assertEqual(len(self.j.records('2026-10-17')),3)

    def test_invalid_clock_or_schedule_rejected(self):
        with self.assertRaises(ValueError):sim.aware('2026-10-17T22:30:00')
        with self.assertRaises(ValueError):sim.SimulatedSchedule(self.j,'2026-10-17',at='25:00')


if __name__=='__main__':unittest.main()
