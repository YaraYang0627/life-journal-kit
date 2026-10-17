#!/usr/bin/env python3
"""Bounded real capture pilot. Invoke with launchd; never installs system settings."""
import argparse
from datetime import datetime, date, timedelta
import json
import os
from zoneinfo import ZoneInfo
from journal import Journal, day
from lark_capture import load_settings, locked_collect, CaptureError


def due_dates(now, start, end):
    start, end = date.fromisoformat(day(start)), date.fromisoformat(day(end))
    if start > end or (end-start).days > 31:
        raise ValueError('pilot_range_invalid')
    if now.tzinfo is None:
        raise ValueError('aware_time_required')
    today = now.date()
    # Capture yesterday on wake; today's summary only after the evening deadline.
    candidates = [today-timedelta(days=1)]
    if (now.hour, now.minute) >= (22, 30):
        candidates.append(today)
    return [d.isoformat() for d in candidates if start <= d <= end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('data', 'config', 'start', 'end'):
        parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        settings = load_settings(args.config)
        now = datetime.now(ZoneInfo(settings['timezone']))
        targets = due_dates(now, args.start, args.end)
        results = [locked_collect(Journal(args.data), settings, d) for d in targets]
        print(json.dumps({'checked_at':now.isoformat(), 'results':results}, ensure_ascii=False))
        return 0 if all(r['status'] in {'saved','unchanged','final_protected'} for r in results) else 2
    except (CaptureError, OSError, ValueError, KeyError, TypeError):
        print('{"status":"configuration_or_storage_error"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
