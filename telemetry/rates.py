"""Observed sync TCP payload only, never all-host traffic.

Two complete snapshots of the observed job sockets/owners/ancestry are required.
Proc visibility/races or parse failures reset coverage globally. Unrelated
unowned sockets are excluded, not evidence of missing job coverage. Lost or
ambiguous ownership of a cookie tied to a job resets that repository, as does
a disappearing or new job socket. Unobserved short-lived connections cannot
be measured. No addresses, argv, paths or raw ss output survive this call.
"""
import math
import os
from pathlib import Path
import pwd
import re
import selectors
import subprocess
import time
import uuid

MAX_OUTPUT = 2 * 1024 * 1024
MAX_PROCESSES = 16384
MAX_SOCKETS = 4096
MAX_COUNTER = 2**53 - 1


def read_ss():
    """Bound memory and wall time, including a hung/overproducing child."""
    child = subprocess.Popen(['/usr/bin/ss', '-Htinpe'], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
    assert child.stdout is not None  # PIPE was explicitly requested.
    deadline = time.monotonic() + 2
    chunks = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError('socket sample timeout')
                data = os.read(child.stdout.fileno(), min(65536, MAX_OUTPUT + 1 - len(chunks)))
                if not data:
                    break
                chunks.extend(data)
                if len(chunks) > MAX_OUTPUT:
                    raise ValueError('socket sample bound')
        if child.wait(timeout=max(0, deadline-time.monotonic())):
            raise ValueError('socket sample failed')
        return chunks.decode('utf-8')
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        child.stdout.close()


def bounded(path, limit):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('proc bound')
    return data


def process(directory):
    raw = bounded(directory / 'stat', 8192)
    fields = raw.decode().rsplit(')', 1)[1].split()
    parent, ticks = int(fields[1]), int(fields[19])
    args = bounded(directory / 'cmdline', 16384)
    uid = directory.stat().st_uid
    if raw != bounded(directory / 'stat', 8192):
        # Compare identity below instead: CPU accounting can change normally.
        check = bounded(directory / 'stat', 8192).decode().rsplit(')', 1)[1].split()
        if (check[1], check[19]) != (fields[1], fields[19]):
            raise ValueError('process changed')
    if args and not args.endswith(b'\0'):
        raise ValueError('incomplete argv')
    return (parent, ticks, uid, args[:-1].decode().split('\0') if args else [], fields[0])


def processes(root):
    result = {}
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_PROCESSES:
                raise ValueError('proc enumeration bound')
            if entry.name.isdecimal():
                result[int(entry.name)] = process(Path(entry.path))
    return result


def attribution(pid, table, jobs):
    """All matching ancestry must agree, with a transport owner, not a probe."""
    owners, identities, visited = set(), [], set()
    current = table.get(pid)
    if current is None:
        raise ValueError('owner disappeared')
    if not current[3] or os.path.basename(current[3][0]) not in ('rsync', 'rsync-ssl', 'openssl', 'ssh'):
        return set(), []
    uid = current[2]
    while pid:
        if pid in visited or len(visited) >= 128:
            raise ValueError('ancestry bound')
        visited.add(pid)
        if pid not in table:
            raise ValueError('incomplete ancestry')
        parent, ticks, actual_uid, args, status = table[pid]
        if status in ('Z', 'X', 'x'):
            raise ValueError('dead owner ancestry')
        if actual_uid != uid:
            break
        identities.append([pid, ticks, actual_uid])
        if args:
            executable = os.path.basename(args[0])
            serving = executable == 'rsync' and any(a in ('--server', '--daemon') or a.startswith('--daemon=') for a in args)
            if serving:
                return set(), []
            for rid, script, expected_uid, roots in jobs:
                if actual_uid != expected_uid:
                    continue
                scripted = args[0] == script or (executable in ('sh', 'bash', 'dash', 'perl', 'python3') and len(args) > 1 and args[1] == script)
                dest = args[-1]
                destination = (executable in ('rsync', 'rsync-ssl') and len(args) >= 3
                               and os.path.isabs(dest) and '..' not in Path(dest).parts
                               and any(os.path.normpath(dest) == r or os.path.normpath(dest).startswith(r + '/') for r in roots))
                if scripted or destination:
                    owners.add(rid)
        pid = parent
    return owners, identities


def parse_ss(text):
    if not isinstance(text, str) or len(text.encode()) > MAX_OUTPUT:
        raise ValueError('socket output bound')
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0].isspace():
            if not records:
                raise ValueError('orphan socket details')
            records[-1] += ' ' + line.strip()
        elif re.match(r'^[A-Z][A-Z0-9-]*\s+\d+\s+\d+\s+\S+\s+\S+(?:\s|$)', line):
            records.append(line)
        else:
            raise ValueError('unrecognized socket row')
        if len(records) > MAX_SOCKETS:
            raise ValueError('socket count bound')
    return records


def sample(config, previous, *, now, proc_root: str | Path = '/proc', ss_reader=None):
    """Return (repository metric mappings, sanitized private numeric state)."""
    rows = {rid: dict(sync_rx_bps=None, sync_tx_bps=None, rates_observed_at=None)
            for rid in config['repositories']}
    root = Path(proc_root)
    try:
        boot = uuid.UUID(bounded(root/'sys/kernel/random/boot_id', 128).decode().strip()).int
        table = processes(root)
        jobs = [(rid, j['script'], pwd.getpwnam(j['user']).pw_uid,
                 {os.path.normpath(j['path']), os.path.realpath(j['path'])})
                for rid, j in config['repositories'].items()]
        records = parse_ss((ss_reader or read_ss)())
        sockets = {rid: {} for rid in rows}
        invalid = set()
        cookie_repos = {}
        uncertain_cookies = set()
        checked = set()
        for record in records:
            pids = set(map(int, re.findall(r'\bpid=(\d+)', record)))
            owners, identities = set(), []
            unattributed = False
            socket_uids = re.findall(r'\buid:(\d+)\b', record)
            for pid in sorted(pids):
                matches, chain = attribution(pid, table, jobs)
                unattributed |= not matches
                if socket_uids and (len(socket_uids) != 1 or int(socket_uids[0]) != table[pid][2]):
                    unattributed = True
                owners.update(matches)
                identities.extend(chain)
            if len(owners) != 1 or unattributed:
                uncertain_cookies.update(str(int(c, 16)) for c in re.findall(r'\bsk:([0-9a-fA-F]+)\b', record))
                invalid.update(owners)
                continue
            rid = next(iter(owners))
            fields = {}
            for name, pattern in (('ino', r'\bino:(\d+)\b'), ('cookie', r'\bsk:([0-9a-fA-F]+)\b'),
                                  ('tx', r'\bbytes_sent:(\d+)\b'), ('rx', r'\bbytes_received:(\d+)\b')):
                values = re.findall(pattern, record)
                if len(values) != 1:
                    invalid.add(rid)
                    break
                fields[name] = int(values[0], 16 if name == 'cookie' else 10)
            else:
                if not fields['ino'] or not fields['cookie'] or any(fields[k] > MAX_COUNTER for k in ('rx', 'tx')):
                    invalid.add(rid)
                    continue
                identity = sorted(set(tuple(i) for i in identities))
                checked.update(i[0] for i in identity)
                key = str(fields['cookie'])
                cookie_repos.setdefault(key, set()).add(rid)
                if len(cookie_repos[key]) > 1:
                    invalid.update(cookie_repos[key])
                value = dict(fields, owners=[list(i) for i in identity])
                if key in sockets[rid] and sockets[rid][key] != value:
                    invalid.add(rid)
                sockets[rid][key] = value
        # Unknown host sockets are outside observed-job coverage. A cookie
        # already tied to a job is not: loss of ownership invalidates that job.
        for key in uncertain_cookies:
            invalid.update(cookie_repos.get(key, ()))
        if isinstance(previous, dict) and previous.get('boot') == boot:
            prior_repos = previous.get('repositories')
            if isinstance(prior_repos, dict):
                invalid.update(rid for rid, prior in prior_repos.items()
                               if isinstance(prior, dict) and uncertain_cookies.intersection(prior))
        for pid in checked:
            current_process = process(root/str(pid))
            if current_process[:4] != table[pid][:4] or current_process[4] in ('Z', 'X', 'x'):
                raise ValueError('ownership changed during sampling')
        if uuid.UUID(bounded(root/'sys/kernel/random/boot_id', 128).decode().strip()).int != boot:
            raise ValueError('boot changed')
        state = {'boot': boot, 'at': now, 'repositories': {r: s for r, s in sockets.items() if s and r not in invalid}}
        if not isinstance(previous, dict) or previous.get('boot') != boot:
            return rows, state
        old_at = previous.get('at')
        if not isinstance(old_at, (int, float)) or isinstance(old_at, bool) or not 0 <= old_at <= MAX_COUNTER or not math.isfinite(old_at) or not 0 < now-old_at <= 30:
            return rows, state
        dt = now-old_at
        old_repos = previous.get('repositories')
        if not isinstance(old_repos, dict):
            return rows, state
        for rid, current in state['repositories'].items():
            old = old_repos.get(rid)
            if not isinstance(old, dict) or set(old) != set(current):
                continue
            rx = tx = 0
            for key, item in current.items():
                prior = old[key]
                if (not isinstance(prior, dict) or any(prior.get(k) != item[k] for k in ('cookie', 'ino', 'owners'))
                        or any(type(prior.get(k)) is not int or not 0 <= prior[k] <= item[k] for k in ('rx', 'tx'))):
                    break
                rx += item['rx']-prior['rx']
                tx += item['tx']-prior['tx']
            else:
                rates = (rx/dt, tx/dt)
                if all(math.isfinite(rate) for rate in rates):
                    rows[rid].update(sync_rx_bps=rates[0], sync_tx_bps=rates[1], rates_observed_at=now)
        return rows, state
    except (OSError, ValueError, UnicodeError, IndexError, KeyError, subprocess.SubprocessError):
        return rows, {}
