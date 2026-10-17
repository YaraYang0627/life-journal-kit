#!/usr/bin/env python3
"""Local journal toolkit. Python standard library only; no network collection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
KINDS = {'event': '发生的事', 'feeling': '当时的感受', 'plan': '还没发生的计划', 'thread': '未完成的事', 'work_summary': '工作总结（二次来源，待本人核对）'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    fd, name = tempfile.mkstemp(prefix='.writing-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def day(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期必须为 YYYY-MM-DD')
    date.fromisoformat(value)
    return value


def month(value):
    day(value + '-01')
    return value


def outside_repo(path):
    path = Path(path).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('用户数据必须放在源码目录之外')
    return path


def markdown(doc):
    lines = ['# ' + doc['date'] + ' · ' + doc['title'], '']
    for section in doc['sections']:
        lines += ['## ' + section['heading'], '']
        for p in section['paragraphs']:
            lines += [p['text'], '', '来源：' + '、'.join(p['sources']), '']
    return '\n'.join(lines)


class Journal:
    def __init__(self, root):
        self.root = outside_repo(root)

    def init(self, timezone='Asia/Shanghai', demo=False):
        ZoneInfo(timezone)
        cfg = self.root / 'config.json'
        if cfg.exists():
            raise ValueError('目录已初始化；不会覆盖配置')
        if self.root.exists() and any(self.root.iterdir()):
            raise ValueError('请选一个新的空目录')
        atomic(cfg, {'schema': 1, 'timezone': timezone, 'demo': demo})

    def config(self):
        cfg = read(self.root / 'config.json')
        if cfg.get('schema') != 1:
            raise ValueError('不支持的数据格式版本')
        return cfg

    def today(self):
        return datetime.now(ZoneInfo(self.config()['timezone'])).date().isoformat()

    def records(self, target):
        path = self.root / 'inbox' / (day(target) + '.json')
        return read(path) if path.exists() else []

    def final_dir(self, target):
        day(target)
        return self.root / 'days' / target[:7] / target

    def add(self, target, text, kind='event', source='本人补充'):
        self.config()
        day(target)
        if kind not in KINDS or not text.strip() or not source.strip():
            raise ValueError('素材正文、类别和来源不能为空或无效')
        record = {'date': target, 'kind': kind, 'text': text.strip(), 'source': source.strip()}
        record['id'] = digest(record)[:16]
        records = self.records(target)
        if any(r['id'] == record['id'] for r in records):
            return {'added': False, 'id': record['id']}
        records.append(record)
        atomic(self.root / 'inbox' / (target + '.json'), records)
        return {'added': True, 'id': record['id'], 'late': self.final_dir(target).exists()}

    def draft(self, target):
        self.config()
        day(target)
        if self.final_dir(target).exists():
            raise ValueError('已有终稿；新增素材保留在收件箱，需另行确认修订，不覆盖原文')
        records = self.records(target)
        if not records:
            raise ValueError('当天没有素材；请先补充，不生成空日记')
        path = self.root / 'drafts' / (target + '.json')
        if path.exists():
            doc = read(path)
            known = {r['id'] for r in doc['sources']}
            added = [r for r in records if r['id'] not in known]
            if not added:
                return path
            for kind, heading in KINDS.items():
                subset = [r for r in added if r['kind'] == kind]
                if subset:
                    doc['sections'].append({'heading': '补充 · ' + heading, 'paragraphs': [
                        {'text': r['text'], 'sources': [r['id']]} for r in subset]})
        else:
            title = records[0]['text'][:22] + ('…' if len(records[0]['text']) > 22 else '')
            doc = {'date': target, 'title': title, 'sections': []}
            for kind, heading in KINDS.items():
                subset = [r for r in records if r['kind'] == kind]
                if subset:
                    doc['sections'].append({'heading': heading, 'paragraphs': [
                        {'text': r['text'], 'sources': [r['id']]} for r in subset]})
        doc['sources'] = records
        doc['fingerprint'] = digest(records)
        atomic(path, doc)
        return path

    def validate(self, doc, target):
        records = self.records(target)
        if doc.get('date') != target or doc.get('sources') != records or doc.get('fingerprint') != digest(records):
            raise ValueError('初稿与当前素材不一致；请重新 draft 合并补充，再审阅')
        if not isinstance(doc.get('title'), str) or not doc['title'].strip():
            raise ValueError('标题不能为空')
        refs = {r['id'] for r in records}
        used = set()
        if not doc.get('sections'):
            raise ValueError('初稿正文不能为空')
        for section in doc['sections']:
            if not isinstance(section.get('heading'), str) or not section['heading'].strip() or not section.get('paragraphs'):
                raise ValueError('段落组不能为空')
            for paragraph in section['paragraphs']:
                if not isinstance(paragraph.get('text'), str) or not paragraph['text'].strip():
                    raise ValueError('段落不能为空')
                sources = paragraph.get('sources', [])
                if not isinstance(sources, list) or not sources or not all(isinstance(s, str) and s in refs for s in sources):
                    raise ValueError('每段需要有效的当日来源引用')
                used.update(sources)
        if used != refs:
            raise ValueError('存在尚未进入初稿的素材；请审阅并合并后再确认')
        return doc

    def finalize(self, target, confirmed=False):
        day(target)
        if not confirmed:
            raise ValueError('需要本人明确确认后使用 --confirm')
        dest = self.final_dir(target)
        draft_path = self.root / 'drafts' / (target + '.json')
        if dest.exists():
            raise ValueError('已有终稿，不允许覆盖')
        doc = self.validate(read(draft_path), target)
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.pending-', dir=dest.parent))
        # Publish both files together. Interrupted staging directories are ignored by readers.
        atomic(staging / 'record.json', doc)
        atomic(staging / 'journal.md', markdown(doc))
        os.rename(staging, dest)
        archive = self.root / 'draft-history' / target[:7]
        archive.mkdir(parents=True, exist_ok=True)
        os.rename(draft_path, archive / (target + '.json'))
        return dest

    def entries(self):
        self.config()
        result = []
        for path in sorted((self.root / 'days').glob('*/*/record.json')):
            if path.parent.name.startswith('.'):
                continue
            doc = read(path)
            day(doc['date'])
            if path.parent != self.final_dir(doc['date']):
                raise ValueError('终稿目录与日期不一致')
            # Detect manual divergence between the frozen structured record and Markdown.
            if (path.parent / 'journal.md').read_text(encoding='utf-8') != markdown(doc):
                raise ValueError('终稿 Markdown 与来源快照不一致；请先核对修订，不能静默忽略')
            result.append(doc)
        return result

    def review(self, target):
        month(target)
        entries = [e for e in self.entries() if e['date'].startswith(target)]
        if not entries:
            raise ValueError('该月没有终稿，不能生成月度回顾')
        evidence = [{'date': e['date'], 'text': p['text'], 'sources': p['sources']}
                    for e in entries for s in e['sections'] for p in s['paragraphs']]
        result = {'month': target, 'count': len(entries), 'through': entries[-1]['date'],
                  'fingerprint': digest(entries), 'sections': [
                      {'heading': '按日回看', 'paragraphs': evidence}]}
        directory = self.root / 'reviews' / target
        directory.mkdir(parents=True, exist_ok=True)
        # Version every review: an AI-edited previous review is never overwritten.
        version = 1
        while (directory / ('v%03d.json' % version)).exists():
            version += 1
        path = directory / ('v%03d.json' % version)
        atomic(path, result)
        return path

    def validate_review(self, review, entries):
        if review.get('fingerprint') != digest(entries):
            return False
        allowed = {e['date']: {r['id'] for r in e['sources']} for e in entries}
        if review.get('count') != len(entries) or review.get('through') != entries[-1]['date']:
            raise ValueError('月报统计与终稿不一致')
        if not review.get('sections'):
            raise ValueError('月报内容为空')
        for section in review['sections']:
            if not section.get('heading') or not section.get('paragraphs'):
                raise ValueError('月报章节为空')
            for p in section['paragraphs']:
                if not p.get('text') or p.get('date') not in allowed or not p.get('sources') or not set(p['sources']) <= allowed[p['date']]:
                    raise ValueError('月报段落缺少有效日期与来源')
        return True

    def build(self):
        entries = self.entries()
        reviews = {}
        for m in sorted({e['date'][:7] for e in entries}):
            paths = sorted((self.root / 'reviews' / m).glob('v[0-9][0-9][0-9].json'))
            if paths:
                review = read(paths[-1])
                subset = [e for e in entries if e['date'].startswith(m)]
                reviews[m] = review if self.validate_review(review, subset) else {'stale': True}
        payload = {'demo': self.config().get('demo', False), 'entries': entries, 'reviews': reviews}
        data = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        html = (ROOT / 'viewer' / 'index.html').read_text(encoding='utf-8')
        html = html.replace('/*__STYLE__*/', (ROOT / 'viewer' / 'style.css').read_text(encoding='utf-8'))
        html = html.replace('/*__SCRIPT__*/', (ROOT / 'viewer' / 'app.js').read_text(encoding='utf-8'))
        html = html.replace('__JOURNAL_DATA__', data)
        path = self.root / 'site' / 'index.html'
        atomic(path, html)
        return path

    def demo(self):
        self.init(demo=True)
        fixture = read(ROOT / 'examples' / 'fictional.json')
        for r in fixture['records']:
            self.add(r['date'], r['text'], r['kind'], '虚构演示 · 主动补充')
        for target in sorted({r['date'] for r in fixture['records']}):
            draft_path = self.draft(target)
            doc = read(draft_path)
            doc['title'] = fixture.get('titles', {}).get(target, doc['title'])
            atomic(draft_path, doc)
            # Fixture-only confirmation; real workflows must ask the owner.
            self.finalize(target, confirmed=True)
        for m in sorted({r['date'][:7] for r in fixture['records']}):
            report_path = self.review(m)
            if m in fixture.get('reviews', {}):
                report = read(report_path)
                report['sections'] = fixture['reviews'][m]
                atomic(report_path, report)
        return self.build()


def serve(path, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?')[0] not in ('/', '/index.html'):
                self.send_error(404)
                return
            content = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print('http://127.0.0.1:%s — 仅本机，Ctrl+C 停止' % server.server_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, help='源码之外的用户数据目录')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init'); init.add_argument('--timezone', default='Asia/Shanghai')
    add = sub.add_parser('add'); add.add_argument('--date', required=True); add.add_argument('--kind', choices=KINDS, default='event')
    add.add_argument('--source', default='本人补充'); add.add_argument('--file', required=True, help='UTF-8 文本；用 - 从 stdin 读取')
    for action in ['draft', 'finalize']:
        command = sub.add_parser(action); command.add_argument('--date', required=True)
        if action == 'finalize': command.add_argument('--confirm', action='store_true')
    review = sub.add_parser('review'); review.add_argument('--month', required=True)
    sub.add_parser('build'); sub.add_parser('demo')
    server = sub.add_parser('serve'); server.add_argument('--port', type=int, default=4318)
    args = parser.parse_args()
    journal = Journal(args.data)
    try:
        if args.command == 'serve':
            serve(journal.build(), args.port)
            return
        # Serialize mutations across local CLI processes (macOS/Linux).
        import fcntl
        journal.root.mkdir(parents=True, exist_ok=True)
        lock_path = journal.root.parent / ('.' + journal.root.name + '.journal.lock')
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.command == 'init': result = journal.init(args.timezone)
            elif args.command == 'add':
                text = sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8')
                result = journal.add(args.date, text, args.kind, args.source)
            elif args.command == 'draft': result = journal.draft(args.date)
            elif args.command == 'finalize': result = journal.finalize(args.date, args.confirm)
            elif args.command == 'review': result = journal.review(args.month)
            elif args.command == 'build': result = journal.build()
            elif args.command == 'demo': result = journal.demo()
        print(result if result is not None else '已初始化')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('未完成：' + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
