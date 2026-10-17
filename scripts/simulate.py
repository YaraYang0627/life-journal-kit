#!/usr/bin/env python3
"""Offline-only collection and clock simulation. No API client or OS scheduler."""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from journal import Journal, atomic, day, digest, read

MARKER = '[JOURNAL_WORK_SUMMARY]'
SUCCESS = {'saved', 'unchanged'}


class SourceFailure(Exception):
    pass


class FixtureSource:
    """Our normalized test contract, not a Feishu HTTP response schema."""
    def __init__(self, payload):
        if payload.get('simulation_only') is not True:
            raise ValueError('仅接受明确标记 simulation_only 的虚构返回数据')
        self.payload = payload

    def messages(self):
        result = []
        for page in self.payload.get('pages', []):
            if page.get('status') != 'ok':
                code = page.get('status')
                raise SourceFailure(code if code in {'timeout', 'unauthorized', 'rate_limited'} else 'source_error')
            if not isinstance(page.get('messages'), list):
                raise SourceFailure('invalid_payload')
            result.extend(page['messages'])
        # Return only after all pages succeeded; never commit partial pagination.
        return result


def select(source, target, zone, chat, sender):
    candidates = {}
    for item in source.messages():
        if not isinstance(item, dict):
            raise SourceFailure('invalid_payload')
        if item.get('chat') != chat or item.get('sender') != sender:
            continue
        if item.get('summary_date') != target or item.get('type') != 'daily_summary':
            continue
        if item.get('marker') != MARKER and item.get('title') != '每日回顾':
            continue
        try:
            updated = datetime.fromisoformat(item['updated_at'])
            created = datetime.fromisoformat(item['created_at'])
            if updated.tzinfo is None or created.tzinfo is None or updated < created:
                raise ValueError()
            # Explicit target date permits a post-midnight summary for yesterday.
            # Reject a supposed summary created before its target local day.
            if created.astimezone(zone).date() < date.fromisoformat(target):
                continue
            if not isinstance(item['id'], str) or not item['id'] or not item['items']:
                raise ValueError()
            for fragment in item['items']:
                if fragment.get('kind') not in {'event', 'plan', 'thread'} or not isinstance(fragment.get('text'), str) or not fragment['text'].strip():
                    raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SourceFailure('invalid_payload')
        old = candidates.get(item['id'])
        if old is None or updated > datetime.fromisoformat(old['updated_at']):
            candidates[item['id']] = item
        elif updated == datetime.fromisoformat(old['updated_at']) and digest(item) != digest(old):
            raise SourceFailure('conflicting_versions')
    marked = [m for m in candidates.values() if m.get('marker') == MARKER]
    selected = marked or list(candidates.values())
    return sorted(selected, key=lambda m: (m['created_at'], m['id']))


class SimulatedCollector:
    def __init__(self, journal, chat='fixture-chat', sender='fixture-bot'):
        self.journal = journal
        self.chat = chat
        self.sender = sender
        self.path = journal.root / 'simulation-state' / 'capture.json'

    def run(self, target, source):
        day(target)
        config = self.journal.config()
        if not config.get('demo'):
            raise ValueError('模拟采集仅可使用独立 demo 数据目录')
        try:
            selected = select(source, target, ZoneInfo(config['timezone']), self.chat, self.sender)
        except SourceFailure as exc:
            return {'date': target, 'status': str(exc), 'committed': False}
        if not selected:
            return {'date': target, 'status': 'missing_summary', 'committed': False}
        scope = digest([self.chat, self.sender, target])
        state = read(self.path) if self.path.exists() else {}
        previous = state.get(scope, {}).get('versions', {})
        versions = {digest(m['id']): digest(m['items']) for m in selected}
        if any(key in previous and previous[key] != version for key, version in versions.items()):
            # Never append contradictory versions into the diary as two completed facts.
            return {'date': target, 'status': 'edited_summary_needs_review', 'committed': False}
        if previous and set(previous) - set(versions) and set(versions) - set(previous):
            return {'date': target, 'status': 'selection_changed_needs_review', 'committed': False}
        fresh = [m for m in selected if digest(m['id']) not in previous]
        if self.journal.final_dir(target).exists():
            return {'date': target, 'status': 'final_protected', 'committed': False}
        if not fresh:
            # Confirm that the output survived before reporting an unchanged capture.
            draft = self.journal.root / 'drafts' / (target + '.json')
            if not draft.exists():
                return {'date': target, 'status': 'saved_output_missing', 'committed': False}
            self.journal.validate(read(draft), target)
            return {'date': target, 'status': 'unchanged', 'committed': True}
        existing = {r['source']: r['text'] for r in self.journal.records(target)}
        for message in fresh:
            for index, fragment in enumerate(message['items']):
                label = '模拟飞书日结（二次来源）/' + digest([message['id'], index])[:16]
                if label in existing and existing[label] != fragment['text'].strip():
                    return {'date': target, 'status': 'edited_summary_needs_review', 'committed': False}
        try:
            for message in fresh:
                for index, fragment in enumerate(message['items']):
                    label = '模拟飞书日结（二次来源）/' + digest([message['id'], index])[:16]
                    self.journal.add(target, fragment['text'], fragment['kind'], label)
            draft = self.journal.draft(target)
            self.journal.validate(read(draft), target)
            # Only hashes and coverage metadata; never raw messages or message IDs.
            state[scope] = {'date': target, 'versions': dict(previous, **versions), 'count': len(dict(previous, **versions))}
            atomic(self.path, state)
        except (OSError, ValueError):
            return {'date': target, 'status': 'save_failed', 'committed': False}
        return {'date': target, 'status': 'saved', 'committed': True, 'messages': len(fresh)}


def aware(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('模拟时间必须包含时区偏移')
    return parsed


class SimulatedSchedule:
    """One-shot ticks with injected time. Does not install or wait for a timer."""
    def __init__(self, journal, start, at='22:30', catchup_days=3, retry_minutes=30):
        self.journal = journal
        self.start = date.fromisoformat(day(start))
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', at):
            raise ValueError('运行时间应为 HH:MM')
        if not 0 <= catchup_days <= 31 or retry_minutes < 1:
            raise ValueError('补跑窗口或重试间隔无效')
        self.at = at
        self.catchup = catchup_days
        self.retry = retry_minutes
        self.path = journal.root / 'simulation-state' / 'schedule.json'

    def tick(self, now, runner):
        if now.tzinfo is None:
            raise ValueError('模拟时钟必须带时区')
        config = self.journal.config()
        if not config.get('demo'):
            raise ValueError('模拟调度仅可使用 demo 数据目录')
        local = now.astimezone(ZoneInfo(config['timezone']))
        first = max(self.start, local.date() - timedelta(days=self.catchup))
        last = local.date() if local.strftime('%H:%M') >= self.at else local.date() - timedelta(days=1)
        state = read(self.path) if self.path.exists() else {}
        result = {'now': local.isoformat(), 'runs': [], 'outside_window': None}
        if self.start < first:
            result['outside_window'] = {'from': self.start.isoformat(), 'through': (first-timedelta(days=1)).isoformat()}
        current = first
        while current <= last:
            key = current.isoformat()
            previous = state.get(key, {})
            current += timedelta(days=1)
            if previous.get('status') in SUCCESS or previous.get('status') in {'final_protected', 'edited_summary_needs_review', 'selection_changed_needs_review'}:
                continue
            if previous.get('retry_at') and now < aware(previous['retry_at']):
                continue
            outcome = runner(key)
            record = {'status': outcome['status'], 'attempts': previous.get('attempts', 0)+1,
                      'last_attempt': now.astimezone(timezone.utc).isoformat()}
            if outcome['status'] not in SUCCESS:
                record['retry_at'] = (now + timedelta(minutes=self.retry)).astimezone(timezone.utc).isoformat()
            state[key] = record
            # If this save fails, next tick retries; collector remains idempotent.
            atomic(self.path, state)
            result['runs'].append(outcome)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--fixture', help='collect/tick 所需的虚构返回文件')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    run = commands.add_parser('collect'); run.add_argument('--date', required=True)
    tick = commands.add_parser('tick'); tick.add_argument('--now', required=True); tick.add_argument('--start', required=True)
    tick.add_argument('--at', default='22:30')
    args = parser.parse_args()
    try:
        j = Journal(args.data)
        if args.command == 'init':
            j.init(demo=True)
            print('已初始化独立模拟目录，不连接账号、不创建系统定时任务。')
            return
        if not args.fixture:
            raise ValueError('需要虚构返回文件')
        source = FixtureSource(read(args.fixture))
        # Share the same lock as the normal journal CLI, with no OS timer installation.
        import fcntl
        j.config()
        with (j.root.parent / ('.'+j.root.name+'.journal.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            collector = SimulatedCollector(j)
            if args.command == 'collect':
                result = collector.run(args.date, source)
            else:
                result = SimulatedSchedule(j,args.start,args.at).tick(aware(args.now),lambda target: collector.run(target,source))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (OSError, ValueError, KeyError, TypeError):
        print('模拟未完成：请检查虚构输入、目标目录及参数。未连接外部账号。',file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
