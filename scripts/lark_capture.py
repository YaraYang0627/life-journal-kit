#!/usr/bin/env python3
"""Opt-in, allowlisted Lark CLI capture; no credentials or message bodies in logs."""
import argparse
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo
from journal import Journal, atomic, day, digest, read, outside_repo


class CaptureError(Exception):
    """Only fixed status codes; never interpolate CLI output."""


def load_settings(path):
    settings = read(outside_repo(path))
    for key in ('chat_id', 'sender_open_bot_id', 'primary_marker', 'cli_path', 'timezone'):
        if not isinstance(settings.get(key), str) or not settings[key]:
            raise CaptureError('invalid_settings')
    if settings.get('enabled') is not True:
        raise CaptureError('capture_disabled')
    if not Path(settings['cli_path']).is_absolute():
        raise CaptureError('cli_path_must_be_absolute')
    ZoneInfo(settings['timezone'])
    return settings


def redact(text):
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{12,}\b', '[已隐藏密钥]', text)
    text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}', 'Bearer [已隐藏]', text)
    text = re.sub(r'(?i)\b(access[_-]?token|refresh[_-]?token|api[_-]?key|password|secret)\s*[:=]\s*[^\s,;]+', r'\1=[已隐藏]', text)
    text = re.sub(r'(?<!\d)\d{17}[\dXx](?!\d)', '[已隐藏证件号]', text)
    return text.replace('\x00', '').strip()


def fetch(settings, target, run=subprocess.run):
    day(target)
    zone = ZoneInfo(settings['timezone'])
    start = datetime.fromisoformat(target).replace(tzinfo=zone)
    end = start + timedelta(days=1)
    argv = [settings['cli_path'], 'im', '+chat-messages-list', '--as', 'user',
            '--chat-id', settings['chat_id'], '--start', start.isoformat(), '--end', end.isoformat(),
            '--order', 'asc', '--page-size', '50', '--page-all', '--page-limit', '10',
            '--no-reactions', '--format', 'json']
    env = os.environ.copy()
    env.update(LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1', TZ=settings['timezone'])
    try:
        response = run(argv, capture_output=True, text=True, encoding='utf-8', env=env, timeout=90)
        payload = json.loads(response.stdout if response.returncode == 0 else response.stderr)
    except subprocess.TimeoutExpired:
        raise CaptureError('timeout')
    except (OSError, ValueError):
        raise CaptureError('cli_unavailable_or_invalid_response')
    if not isinstance(payload, dict):
        raise CaptureError('invalid_response')
    if response.returncode or payload.get('ok') is not True:
        error = payload.get('error') or {}
        if not isinstance(error, dict):
            raise CaptureError('source_error')
        if error.get('type') == 'authorization':
            raise CaptureError('authorization_required')
        raise CaptureError('source_error')
    data = payload.get('data', {})
    meta = payload.get('meta') or {}
    if not isinstance(data, dict) or not isinstance(meta, dict):
        raise CaptureError('invalid_response')
    pagination = meta.get('pagination') or {}
    if not isinstance(pagination, dict):
        raise CaptureError('invalid_response')
    if pagination.get('complete') is not True or data.get('has_more') is not False:
        raise CaptureError('incomplete_pagination')
    messages = data.get('messages')
    if not isinstance(messages, list):
        raise CaptureError('invalid_response')
    candidates = {}
    for message in messages:
        if not isinstance(message, dict) or message.get('deleted'):
            continue
        if message.get('chat_id') != settings['chat_id']:
            continue
        sender = message.get('sender') or {}
        if not isinstance(sender, dict) or sender.get('open_bot_id') != settings['sender_open_bot_id']:
            continue
        content = message.get('content')
        if not isinstance(content, str):
            continue
        primary = settings['primary_marker'] in content
        fallback = '每日回顾' in content
        if not (primary or fallback):
            continue
        if message.get('msg_type') not in {'text', 'post', 'interactive'}:
            raise CaptureError('unsupported_summary_type')
        try:
            created = datetime.fromisoformat(message['create_time'])
            if created.tzinfo is None:
                created = created.replace(tzinfo=zone)
            if not start <= created < end:
                continue
        except (KeyError, TypeError, ValueError):
            raise CaptureError('invalid_timestamp')
        # Explicit ISO date in a heading is authoritative; reject another day's recap.
        heading = content.splitlines()[0] if content.splitlines() else ''
        dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', heading)
        if dates and target not in dates:
            continue
        identifier = message.get('message_id')
        if not isinstance(identifier, str) or not identifier:
            raise CaptureError('missing_message_id')
        clean = redact(content)
        if not clean:
            continue
        item = {'id_hash': digest(identifier), 'version': digest(content), 'text': clean, 'primary': primary}
        if identifier in candidates and candidates[identifier]['version'] != item['version']:
            raise CaptureError('conflicting_versions')
        candidates[identifier] = item
    selected = [m for m in candidates.values() if m['primary']] or list(candidates.values())
    return {'messages': selected, 'visible_count': len(messages), 'complete': True}


def collect(journal, settings, target, fetcher=fetch):
    day(target)
    cfg = journal.config()
    if cfg.get('demo'):
        raise CaptureError('real_capture_refuses_demo_data')
    if cfg['timezone'] != settings['timezone']:
        raise CaptureError('timezone_mismatch')
    if journal.final_dir(target).exists():
        return {'date': target, 'status': 'final_protected', 'committed': False}
    try:
        result = fetcher(settings, target)
    except CaptureError as exc:
        return {'date': target, 'status': str(exc), 'committed': False}
    selected = result['messages']
    if not selected:
        return {'date': target, 'status': 'missing_summary', 'committed': False}
    path = journal.root / 'capture-state' / 'lark.json'
    state = read(path) if path.exists() else {}
    scope = digest([settings['chat_id'], settings['sender_open_bot_id'], target])
    previous = state.get(scope, {}).get('versions', {})
    versions = {m['id_hash']: m['version'] for m in selected}
    if any(k in previous and previous[k] != v for k, v in versions.items()) or set(previous) - set(versions):
        return {'date': target, 'status': 'source_changed_needs_review', 'committed': False}
    existing = {r['source']: r for r in journal.records(target)}
    for m in selected:
        label = '飞书工作总结（二次来源）/' + m['id_hash']
        if label in existing and existing[label]['text'] != m['text']:
            return {'date': target, 'status': 'source_changed_needs_review', 'committed': False}
    try:
        for m in selected:
            journal.add(target, m['text'], 'work_summary', '飞书工作总结（二次来源）/' + m['id_hash'])
        draft_path = journal.draft(target)
        journal.validate(read(draft_path), target)
        state[scope] = {'date': target, 'versions': versions, 'count': len(selected)}
        atomic(path, state)
    except (OSError, ValueError):
        return {'date': target, 'status': 'save_failed', 'committed': False}
    return {'date': target, 'status': 'unchanged' if versions == previous else 'saved',
            'committed': True, 'messages': len(selected), 'pagination_complete': True}


def locked_collect(journal, settings, target):
    lockpath = journal.root.parent / ('.'+journal.root.name+'.journal.lock')
    with lockpath.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return collect(journal, settings, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--date', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        settings = load_settings(args.config)
        result = locked_collect(Journal(args.data), settings, args.date)
        print(json.dumps(result, ensure_ascii=False))
        if result['status'] not in {'saved', 'unchanged', 'final_protected'}:
            sys.exit(2)
    except (CaptureError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'status':'configuration_or_storage_error','committed':False}))
        sys.exit(1)


if __name__ == '__main__':
    main()
