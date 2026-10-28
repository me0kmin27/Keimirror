"""Fail-closed nginx live counters; public names retain collector ABI.

Readers are injectable: reader() returns JSON bytes, workers_reader(proc_root)
returns {master: [pid,start_ticks], workers: [[pid,start_ticks],...], boot: int}.
No dead registration slots are accepted: the ABI must publish exactly the live
worker PID set, including gracefully shutting-down workers. Epoch is hashed
before persistence; raw payloads, paths, addresses and headers never survive.
"""
import hashlib
import json
import math
import time
import re
import socket
import os
from pathlib import Path
import stat
import uuid

PID_PATH = '/run/nginx.pid'
TRUSTED_UID = 0
SOCKET_PATH = '/run/keimirror-live/status.sock'
MAX_HEADER = 4096

MAX_BYTES = 32768
MAX_WORKERS = 1024
MAX_COUNTER = 2**64 - 1
TIMEOUT = .3


def uint(value, maximum=MAX_COUNTER):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('invalid unsigned integer')
    return value


def keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError('invalid fields')


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def identity(value):
    keys(value, ('master', 'workers', 'boot'))
    uint(value['boot'], 2**128-1)
    def pair(p):
        if type(p) is not list or len(p) != 2 or not uint(p[0], 2**31-1) or not uint(p[1]):
            raise ValueError('invalid process identity')
        return list(p)
    master = pair(value['master'])
    workers = value['workers']
    if type(workers) is not list or not 1 <= len(workers) <= MAX_WORKERS:
        raise ValueError('invalid workers')
    workers = sorted(pair(p) for p in workers)
    if len({p[0] for p in workers}) != len(workers) or master[0] in {p[0] for p in workers}:
        raise ValueError('duplicate identity')
    return dict(master=master, workers=workers, boot=value['boot'])


def counters(value, rids):
    keys(value, rids)
    result = {}
    for rid in rids:
        row = value[rid]
        keys(row, ('rx_bytes', 'tx_bytes', 'uncertain', 'active_uncertain'))
        result[rid] = {k: uint(v) for k, v in row.items()}
    return result


def decode(raw, rids, workers):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError('invalid body')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    keys(value, ('schema_version', 'epoch', 'workers', 'repositories'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('invalid version')
    epoch = value['epoch']
    if type(epoch) is not str or not 1 <= len(epoch.encode()) <= 256:
        raise ValueError('invalid epoch')
    pids = value['workers']
    if type(pids) is not list or not 1 <= len(pids) <= MAX_WORKERS:
        raise ValueError('invalid registrations')
    if any(not uint(p, 2**31-1) for p in pids) or len(set(pids)) != len(pids):
        raise ValueError('invalid PID')
    if set(pids) != {p[0] for p in workers['workers']}:
        raise ValueError('incomplete coverage')
    return hashlib.sha256(epoch.encode()).hexdigest(), counters(value['repositories'], rids)


def finite(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid time')
    return value


def prior(value, rids):
    keys(value, ('epoch', 'identity', 'monotonic', 'repositories'))
    epoch = value['epoch']
    if type(epoch) is not str or len(epoch) != 64 or any(c not in '0123456789abcdef' for c in epoch):
        raise ValueError('invalid saved epoch')
    return dict(epoch=epoch, identity=identity(value['identity']),
                monotonic=finite(value['monotonic']), repositories=counters(value['repositories'], rids))


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise ValueError('sample deadline')
    return value


def read_http(deadline):
    """Fixed Unix HTTP endpoint, no redirects, bounded framing and deadline."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining(deadline))
        connection.connect(SOCKET_PATH)
        connection.settimeout(remaining(deadline))
        connection.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        raw = bytearray()
        while b'\r\n\r\n' not in raw:
            connection.settimeout(remaining(deadline))
            chunk = connection.recv(min(1024, MAX_HEADER + 1 - len(raw)))
            if not chunk:
                raise ValueError('truncated header')
            raw.extend(chunk)
            if len(raw) > MAX_HEADER and b'\r\n\r\n' not in raw:
                raise ValueError('header bound')
        header, body = bytes(raw).split(b'\r\n\r\n', 1)
        if len(header) + 4 > MAX_HEADER:
            raise ValueError('header bound')
        lines = header.split(b'\r\n')
        if not re.fullmatch(rb'HTTP/1\.[01] 200(?: [\x20-\x7e]*)?', lines[0]):
            raise ValueError('HTTP status')
        fields = {}
        for line in lines[1:]:
            match = re.fullmatch(rb"([!#$%&'*+.^_`|~0-9A-Za-z-]+):[ \t]*([\x20-\x7e\t]*)", line)
            if not match or match[1].lower() in fields:
                raise ValueError('invalid header')
            fields[match[1].lower()] = match[2].strip()
        length = fields.get(b'content-length', b'')
        if b'transfer-encoding' in fields or not re.fullmatch(rb'[0-9]{1,5}', length):
            raise ValueError('invalid framing')
        length = int(length)
        if not 0 < length <= MAX_BYTES:
            raise ValueError('body bound')
        body = bytearray(body)
        while len(body) < length:
            connection.settimeout(remaining(deadline))
            chunk = connection.recv(min(4096, length + 1 - len(body)))
            if not chunk:
                raise ValueError('truncated body')
            body.extend(chunk)
        if len(body) != length:
            raise ValueError('extra body')
        return bytes(body)


def bounded(path, limit, deadline, trusted=False):
    remaining(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('not regular')
        if trusted and (info.st_uid != TRUSTED_UID or info.st_mode & 0o022):
            raise ValueError('untrusted pid file')
        result = os.read(fd, limit + 1)
        if len(result) > limit:
            raise ValueError('proc bound')
        remaining(deadline)
        return result
    finally:
        os.close(fd)


def process(root, pid, deadline):
    directory = root / str(pid)
    def fields():
        raw = bounded(directory / 'stat', 8192, deadline).decode('ascii')
        opening = raw.find(' (')
        if opening < 1 or raw[:opening] != str(pid):
            raise ValueError('bad proc PID')
        parts = raw.rsplit(')', 1)[-1].split()
        if len(parts) < 20 or parts[0] in ('Z', 'X', 'x'):
            raise ValueError('dead process')
        if not parts[1].isdigit() or not parts[19].isdigit():
            raise ValueError('bad proc stat')
        return uint(int(parts[1]), 2**31-1), uint(int(parts[19]))
    first = fields()
    command = bounded(directory / 'cmdline', 8192, deadline)
    if not command or not command.endswith(b'\0'):
        raise ValueError('invalid command')
    title = command.rstrip(b'\0').decode('utf-8')
    exe = directory / 'exe'
    target = os.readlink(exe)
    executable = exe.stat()
    if (Path(target).name != 'nginx' or executable.st_uid != TRUSTED_UID
            or executable.st_mode & 0o022 or not stat.S_ISREG(executable.st_mode)):
        raise ValueError('untrusted nginx executable')
    uid = directory.stat().st_uid
    if first != fields():
        raise ValueError('reused PID')
    return first, title, uid, executable.st_dev, executable.st_ino


def read_workers(proc_root, deadline):
    """Two bounded child enumerations and per-process identity rechecks.

    Unknown child roles fail closed rather than silently excluding a new nginx
    worker title. Cache manager/loader processes are recognized, not counted.
    """
    root = Path(proc_root)
    def master_pid():
        raw = bounded(PID_PATH, 32, deadline, trusted=True).strip()
        if not re.fullmatch(rb'[1-9][0-9]{0,9}', raw):
            raise ValueError('invalid master PID')
        return uint(int(raw), 2**31-1)
    pid = master_pid()
    master = process(root, pid, deadline)
    if master[2] != TRUSTED_UID or not master[1].startswith('nginx: master process '):
        raise ValueError('untrusted master')
    path = root / str(pid) / 'task' / str(pid) / 'children'
    def children():
        tokens = bounded(path, MAX_WORKERS * 12, deadline).split()
        if len(tokens) > MAX_WORKERS or any(not re.fullmatch(rb'[1-9][0-9]{0,9}', p) for p in tokens):
            raise ValueError('children bound')
        pids = [uint(int(p), 2**31-1) for p in tokens]
        if len(set(pids)) != len(pids):
            raise ValueError('duplicate children')
        return sorted(pids)
    first = children()
    processes, workers = {}, []
    for child in first:
        item = process(root, child, deadline)
        if item[0][0] != pid or item[3:] != master[3:]:
            raise ValueError('invalid child ancestry')
        title = item[1]
        if title in ('nginx: worker process', 'nginx: worker process is shutting down'):
            workers.append([child, item[0][1]])
        elif title not in ('nginx: cache manager process', 'nginx: cache loader process'):
            raise ValueError('unknown nginx child')
        processes[child] = item
    if first != children():
        raise ValueError('children changed')
    for child, item in processes.items():
        if item != process(root, child, deadline):
            raise ValueError('child changed')
    if first != children() or master != process(root, pid, deadline) or pid != master_pid():
        raise ValueError('master changed')
    boot = uuid.UUID(bounded(root / 'sys/kernel/random/boot_id', 128, deadline).decode().strip()).int
    return identity(dict(master=[pid, master[0][1]], workers=workers, boot=boot))


def sample(config, previous, *, now=None, proc_root='/proc', reader=None,
           workers_reader=None, monotonic_now=None):
    """Return metric mapping and sanitized baseline; failure discards baseline."""
    rows = {rid: dict(sync_rx_bps=None, sync_tx_bps=None, rates_observed_at=None)
            for rid in config['repositories']}
    try:
        deadline = time.monotonic() + TIMEOUT
        before = identity(workers_reader(proc_root) if workers_reader else read_workers(proc_root, deadline))
        remaining(deadline)
        epoch, values = decode(reader() if reader else read_http(deadline), rows, before)
        remaining(deadline)
        after = identity(workers_reader(proc_root) if workers_reader else read_workers(proc_root, deadline))
        remaining(deadline)
        if before != after:
            raise ValueError('worker race')
        monotonic = finite(time.monotonic() if monotonic_now is None else monotonic_now)
        observed = finite(time.time() if now is None else now)
        state = dict(epoch=epoch, identity=after, monotonic=monotonic, repositories=values)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return rows, {}
    try:
        old = prior(previous, rows)
        dt = monotonic - old['monotonic']
        if not 0 < dt <= 2 or old['epoch'] != epoch or old['identity'] != after:
            return rows, state
        for rid, row in rows.items():
            a, b = old['repositories'][rid], values[rid]
            if (a['active_uncertain'] or b['active_uncertain'] or a['uncertain'] != b['uncertain']
                    or b['rx_bytes'] < a['rx_bytes'] or b['tx_bytes'] < a['tx_bytes']):
                continue
            rx, tx = (b['rx_bytes']-a['rx_bytes'])/dt, (b['tx_bytes']-a['tx_bytes'])/dt
            if math.isfinite(rx) and math.isfinite(tx):
                row.update(sync_rx_bps=rx, sync_tx_bps=tx, rates_observed_at=observed)
    except (ValueError, TypeError, KeyError, OverflowError):
        pass
    return rows, state
