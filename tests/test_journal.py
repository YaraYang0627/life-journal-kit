import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('journal', ROOT / 'scripts/journal.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.j = m.Journal(Path(self.temp.name) / 'personal')
        self.j.init()

    def tearDown(self):
        self.temp.cleanup()

    def add_day(self, target='2026-10-01', text='虚构：读了十页书。'):
        self.j.add(target, text)
        return self.j.draft(target)

    def final(self, target='2026-10-01', text='虚构：读了十页书。'):
        self.add_day(target, text)
        return self.j.finalize(target, True)

    def test_full_flow_and_draft_archive(self):
        folder=self.final()
        self.assertTrue((folder/'journal.md').exists())
        self.assertFalse((self.j.root/'drafts/2026-10-01.json').exists())
        self.assertTrue((self.j.root/'draft-history/2026-10/2026-10-01.json').exists())
        report=m.read(self.j.review('2026-10'))
        self.assertEqual(report['count'],1)
        page=self.j.build().read_text()
        self.assertIn('虚构：读了十页书。',page)
        self.assertNotIn('__JOURNAL_DATA__',page)

    def test_duplicate_input_and_draft_are_idempotent(self):
        p=self.add_day(); before=p.read_bytes()
        self.assertFalse(self.j.add('2026-10-01','虚构：读了十页书。')['added'])
        self.j.draft('2026-10-01')
        self.assertEqual(before,p.read_bytes())

    def test_late_addition_preserves_edited_draft(self):
        p=self.add_day(); doc=m.read(p);doc['title']='读书的一天';m.atomic(p,doc)
        self.j.add('2026-10-01','虚构：晚饭后又读了两页。')
        with self.assertRaisesRegex(ValueError,'不一致'):self.j.finalize('2026-10-01',True)
        self.j.draft('2026-10-01');doc=m.read(p)
        self.assertEqual(doc['title'],'读书的一天');self.assertEqual(len(doc['sources']),2)
        self.j.finalize('2026-10-01',True)
        self.assertFalse((self.j.root/'days/2026-10/2026-10-02').exists())

    def test_no_material_no_diary(self):
        with self.assertRaisesRegex(ValueError,'没有素材'):self.j.draft('2026-10-02')
        self.assertEqual(self.j.entries(),[])
        self.assertTrue(self.j.build().exists())

    def test_confirmation_required(self):
        self.add_day()
        with self.assertRaisesRegex(ValueError,'确认'):self.j.finalize('2026-10-01')
        self.assertEqual(self.j.entries(),[])

    def test_final_never_overwritten(self):
        folder=self.final();before=(folder/'record.json').read_bytes()
        result=self.j.add('2026-10-01','虚构：晚到的补充。')
        self.assertTrue(result['late'])
        with self.assertRaises(ValueError):self.j.draft('2026-10-01')
        with self.assertRaises(ValueError):self.j.finalize('2026-10-01',True)
        self.assertEqual(before,(folder/'record.json').read_bytes())

    def test_write_failure_does_not_publish_half_final(self):
        self.add_day(); original=m.atomic
        def failure(path,value):
            if Path(path).name=='journal.md':raise OSError('simulated disk failure')
            return original(path,value)
        with patch.object(m,'atomic',failure):
            with self.assertRaises(OSError):self.j.finalize('2026-10-01',True)
        self.assertEqual(self.j.entries(),[])
        self.assertTrue((self.j.root/'drafts/2026-10-01.json').exists())
        self.j.finalize('2026-10-01',True)
        self.assertEqual(len(self.j.entries()),1)

    def test_invalid_citation_blocked(self):
        p=self.add_day();doc=m.read(p);doc['sections'][0]['paragraphs'][0]['sources']=['unknown'];m.atomic(p,doc)
        with self.assertRaisesRegex(ValueError,'引用'):self.j.finalize('2026-10-01',True)

    def test_missing_source_blocked(self):
        self.add_day();self.j.add('2026-10-01','虚构：又一条事实。');p=self.j.draft('2026-10-01')
        doc=m.read(p);doc['sections'].pop();m.atomic(p,doc)
        with self.assertRaisesRegex(ValueError,'尚未进入'):self.j.finalize('2026-10-01',True)

    def test_month_boundary_count_and_review_version(self):
        self.final('2026-09-30');self.final('2026-10-01')
        a=self.j.review('2026-09');b=self.j.review('2026-09')
        self.assertNotEqual(a,b);self.assertTrue(a.exists());self.assertEqual(m.read(a)['count'],1)
        self.assertEqual(m.read(a)['through'],'2026-09-30')

    def test_stale_review_hidden(self):
        self.final();self.j.review('2026-10');self.final('2026-10-03')
        page=self.j.build().read_text()
        self.assertIn('"stale": true',page)

    def test_review_bad_citation_blocked(self):
        self.final();p=self.j.review('2026-10');doc=m.read(p)
        doc['sections'][0]['paragraphs'][0]['date']='2026-10-02';m.atomic(p,doc)
        with self.assertRaisesRegex(ValueError,'来源'):self.j.build()

    def test_html_injection_stays_data(self):
        self.final(text='虚构：<script>alert(1)</script><img src=x onerror=alert(2)>')
        html=self.j.build().read_text()
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('\\u003cscript\\u003e',html)
        self.assertIn("connect-src 'none'",html)

    def test_dates_and_source_tree_protection(self):
        for value in ['2026-02-30','../bad','2026-1-1']:
            with self.assertRaises(ValueError):self.j.add(value,'x')
        with self.assertRaises(ValueError):m.Journal(ROOT/'data')
        with self.assertRaises(ValueError):self.j.init()

    def test_markdown_divergence_is_reported(self):
        folder=self.final();(folder/'journal.md').write_text('changed')
        with self.assertRaisesRegex(ValueError,'不一致'):self.j.build()

    def test_demo_has_empty_days_and_explicit_feelings_only(self):
        demo=m.Journal(Path(self.temp.name)/'demo');demo.demo()
        self.assertTrue(demo.config()['demo'])
        entries=demo.entries();dates={e['date'] for e in entries}
        self.assertNotIn('2026-10-03',dates)
        self.assertFalse(any(r['kind']=='feeling' for e in entries if e['date']=='2026-10-06' for r in e['sources']))
        self.assertEqual(len(dates),len(entries))


if __name__=='__main__':unittest.main()
