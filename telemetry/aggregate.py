#!/usr/bin/env python3
"""Sanitize mirror status for the same-origin public homepage endpoint."""

import argparse
import json
import math
import os
from pathlib import Path
import ssl
import tempfile
import time
from typing import Any
import urllib.request

GROUPS = {
    'archlinux': ['archlinux'], 'gentoo': ['gentoo', 'gentoo-portage'],
    'kali': ['kali', 'kali-images'], 'cachyos': ['cachyos'],
    'artix': ['artix', 'artix-iso', 'artix-weekly-iso'],
    'opnsense': ['opnsense'], 'endeavouros': ['endeavouros'],
    'fyralabs': ['fyralabs'], 'manjaro': ['manjaro'], 'proxmox': ['proxmox'],
}
FIELDS = ('state', 'last_attempt_at', 'last_finished_at', 'last_success_at',
          'exit_code', 'warning', 'size_bytes', 'size_measured_at',
          'sync_rx_bps', 'sync_tx_bps', 'rates_observed_at',
          'http_bytes_60s', 'rsync_bytes_60s', 'traffic_observed_since')


MAX_SAFE = 2**53 - 1
STATES = ('syncing', 'failed', 'unknown', 'completed')
TIME_FIELDS = ('last_attempt_at', 'last_finished_at', 'last_success_at',
               'size_measured_at', 'rates_observed_at', 'traffic_observed_since')
BYTE_FIELDS = ('size_bytes', 'http_bytes_60s', 'rsync_bytes_60s')


def number(value, maximum=MAX_SAFE, integer=False):
    valid_type = type(value) is int if integer else type(value) in (int, float)
    if not valid_type or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError('Invalid numeric metric')
    return value


def clean_metric(value, now):
    if not isinstance(value, dict) or not set(FIELDS) <= value.keys():
        raise ValueError('Missing metrics')
    if value['state'] not in STATES or type(value['warning']) is not bool:
        raise ValueError('Invalid state')
    clean = {key: value[key] for key in FIELDS}
    for key in TIME_FIELDS:
        if clean[key] is not None:
            number(clean[key], now + 30)
    for key in BYTE_FIELDS:
        if clean[key] is not None:
            number(clean[key], integer=True)
    for key in ('sync_rx_bps', 'sync_tx_bps'):
        if clean[key] is not None:
            number(clean[key])
    if clean['exit_code'] is not None:
        number(clean['exit_code'], 255, integer=True)
    if clean['state'] == 'completed':
        accepted_exit = clean['exit_code'] == 0 or (clean['exit_code'] == 24 and clean['warning'])
        if not accepted_exit or clean['last_success_at'] is None:
            raise ValueError('Completion has no successful result evidence')
    return clean


def sanitize_snapshot(value, mirror_id, now) -> dict[str, Any]:
    if not isinstance(value, dict) or mirror_id not in ('mirror1', 'mirror2'):
        raise ValueError('Invalid mirror document')
    if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported schema')
    if value.get('mirror_id') != mirror_id:
        raise ValueError('Wrong mirror')
    observed = number(value.get('collected_at'), now + 30)
    interval = number(value.get('interval_seconds'), 60, integer=True)
    raw_projects = value.get('projects')
    if not interval or not isinstance(raw_projects, dict) or set(raw_projects) != set(GROUPS):
        raise ValueError('Invalid project coverage')
    projects = {}
    for project, repositories in GROUPS.items():
        data = raw_projects[project]
        clean = clean_metric(data, now)
        rows = data.get('repositories')
        if not isinstance(rows, list) or len(rows) != len(repositories):
            raise ValueError('Invalid repository coverage')
        by_id = {}
        for row in rows:
            if not isinstance(row, dict) or row.get('id') not in repositories or row['id'] in by_id:
                raise ValueError('Invalid repository identity')
            by_id[row['id']] = dict(id=row['id'], **clean_metric(row, now))
        clean['repositories'] = [by_id[repo] for repo in repositories]
        # Rollout compatibility: retain legacy status, but its 15-second rates
        # are not latest-sample telemetry. Never relabel those values as 1 Hz.
        if interval != 1:
            for metric in (clean, *clean['repositories']):
                metric.update(sync_rx_bps=None, sync_tx_bps=None, rates_observed_at=None)
        states = {row['state'] for row in clean['repositories']}
        if clean['state'] != next(state for state in STATES if state in states):
            raise ValueError('Inconsistent group state')
        projects[project] = clean
    return dict(schema_version=1, mirror_id=mirror_id, collected_at=observed,
                interval_seconds=interval, projects=projects)


ENDPOINTS = {
    'mirror1': 'https://mirror.keiminem.com/.well-known/keimirror-status.json',
    'mirror2': 'https://mirror2.keiminem.com/.well-known/keimirror-status.json',
}
MAX_BODY = 262144


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_snapshot(mirror_id, opener=None):
    if mirror_id not in ENDPOINTS:
        raise ValueError('Unknown mirror')
    if opener is None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    request = urllib.request.Request(ENDPOINTS[mirror_id], headers={
        'Accept': 'application/json', 'Cache-Control': 'no-cache',
        'User-Agent': 'KeimirrorStatus/1.0 (+https://keiminem.com)',
    })
    with opener.open(request, timeout=4) as response:
        content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
        if response.status != 200 or content_type != 'application/json':
            raise ValueError('Unexpected status response')
        body = response.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise ValueError('Oversized status response')
    return json.loads(body)


def collect(fetcher=None, previous=None, now=None):
    realtime = now is None
    now = time.time() if now is None else now
    fetcher = fetch_snapshot if fetcher is None else fetcher
    mirrors = {}
    for mirror_id in ENDPOINTS:
        old = None
        try:
            if previous is not None:
                old = sanitize_snapshot(previous['mirrors'][mirror_id]['snapshot'], mirror_id, now)
        except (TypeError, KeyError, ValueError, OverflowError):
            pass
        entry = {'available': False, 'snapshot': old}
        try:
            current = sanitize_snapshot(fetcher(mirror_id), mirror_id, now)
            if old is None or current['collected_at'] >= old['collected_at']:
                entry = {'available': now - current['collected_at'] <= 5, 'snapshot': current}
        except Exception:
            # Never relay transport/parser exceptions, raw data or a new timestamp
            # for the retained old sample. The other mirror remains independent.
            pass
        mirrors[mirror_id] = entry
    published_at = time.time() if realtime else now
    for entry in mirrors.values():
        if entry['available']:
            entry['available'] = published_at - entry['snapshot']['collected_at'] <= 5
    return {'schema_version': 1, 'generated_at': published_at, 'refresh_seconds': 1, 'mirrors': mirrors}


def publish(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary = tempfile.mkstemp(prefix='.status-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv=None, fetcher=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    previous = None
    try:
        with open(args.output, 'rb') as stream:
            payload = stream.read(MAX_BODY * 2 + 1)
        if len(payload) <= MAX_BODY * 2:
            previous = json.loads(payload)
    except (OSError, ValueError):
        pass
    result = collect(fetcher=fetcher, previous=previous, now=now)
    publish(args.output, result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
