"""Current observed live transfer rate: upstream sync + native rsync delivery.

Host-perspective TCP_INFO bytes_received / bytes_sent deltas (B/s); sent includes
retransmitted payload. Not NIC bytes, completed files, HTTP, or exact interval
traffic. A connection born and closed between polls is invisible. Two adjacent,
complete observations with the same socket/holder identities are required;
churn, loss, counter regression and windows outside (0, 2] seconds are unknown.

Production joins bounded INET_DIAG counters to ss ownership by cookie AND inode,
then verifies holder FDs, PID/start identity and network namespace. Upstream's
exact script/destination ancestry and UID checks are unchanged. Delivery is a
separate native-chroot classifier: root daemon ancestry, canonical configured
module root, cwd containment, root device/inode, held socket FD, and only the
verified root->65534 credential transition. Nonchroot delivery is unsupported.
A proven daemon child with root / is ambiguous and invalidates all repositories.

Complete empty endpoint observations may report zero ONLY with the above
observed-live meaning, never an all-byte interval-idle claim. Ownerless/unrelated
sockets do not invalidate known repository sockets; unknown live ownership
prevents empty-zero claims, and a lost previously attributed cookie invalidates
its repository. Proc visibility/parse failures reset coverage, never synthesize
zero. Private state contains only allowlisted repository IDs and numeric socket,
process, root and namespace identities; no addresses, argv or paths.
"""
import configparser
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
# A delayed tick is unknown, never a long-window rate labelled as live.
MAX_SAMPLE_SECONDS = 2


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


def attribution(pid, table, jobs, *, dependencies=None):
    """All matching ancestry must agree, with a transport owner, not a probe."""
    owners, identities, visited = set(), [], set()
    if dependencies is not None:
        dependencies.add(pid)
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
        if dependencies is not None:
            # The UID boundary is read even though it is not an owner.
            dependencies.add(pid)
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


def decode_diag(data):
    """Decode Linux inet_diag_msg + rtattrs; no address data escapes."""
    import struct
    if len(data) < 72 or data[0] not in (2, 10):
        raise ValueError('short diagnostic')
    result = dict(ino=struct.unpack_from('=I', data, 68)[0],
                  cookie=struct.unpack_from('=Q', data, 44)[0],
                  uid=struct.unpack_from('=I', data, 64)[0],
                  state=data[1], rx=None, tx=None)
    offset, found = 72, False
    while offset < len(data):
        if len(data)-offset < 4:
            raise ValueError('short attribute')
        length, kind = struct.unpack_from('=HH', data, offset)
        if length < 4 or offset+length > len(data):
            raise ValueError('truncated attribute')
        if kind == 2:
            if found:
                raise ValueError('duplicate TCP_INFO')
            found = True
            info = data[offset+4:offset+length]
            if len(info) >= 208:
                result.update(rx=struct.unpack_from('=Q', info, 128)[0],
                              tx=struct.unpack_from('=Q', info, 200)[0])
        offset += (length+3) & ~3
    return result


def read_diag():
    """Bounded IPv4/IPv6 NETLINK_SOCK_DIAG dump, explicit TCP_INFO zeros.

    Reject incomplete/interrupted multipart dumps; do not fall back to ss
    missing-counter guesses. Listening sockets are outside transfer coverage.
    """
    import socket
    import struct
    deadline = time.monotonic() + 2
    result, total = [], 0
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 4) as sock:
        sock.bind((0, 0))
        for seq, family in enumerate((socket.AF_INET, socket.AF_INET6), 1):
            request = struct.pack('=BBBBI', family, socket.IPPROTO_TCP, 2, 0,
                                  0xFFF & ~(1 << 10)) + bytes(40) + b'\xff'*8
            sock.sendto(struct.pack('=IHHII', 16+len(request), 20, 0x301, seq, 0)+request, (0, 0))
            done = False
            while not done:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise ValueError('diagnostic timeout')
                sock.settimeout(remaining)
                data, ancillary, flags, address = sock.recvmsg(65536)
                total += len(data)
                if flags & socket.MSG_TRUNC or address[0] != 0 or total > MAX_OUTPUT or not data:
                    raise ValueError('diagnostic bounds or sender')
                offset = 0
                while offset < len(data):
                    if len(data)-offset < 16:
                        raise ValueError('short netlink header')
                    length, kind, msgflags, sequence, port = struct.unpack_from('=IHHII', data, offset)
                    if length < 16 or offset+length > len(data) or sequence != seq or msgflags & 0x10:
                        raise ValueError('incomplete diagnostic dump')
                    body = data[offset+16:offset+length]
                    if kind == 3:
                        if len(body) < 4 or struct.unpack_from('=i', body)[0] != 0:
                            raise ValueError('diagnostic completion error')
                        done = True
                    elif kind == 20 and msgflags & 2 and not done:
                        result.append(decode_diag(body))
                        if len(result) > MAX_SOCKETS:
                            raise ValueError('socket count bound')
                    else:
                        raise ValueError('diagnostic error or unexpected message')
                    offset += (length+3) & ~3
    return result


def root_identity(directory):
    """Private, transient root/cwd fingerprint; absence is never attribution."""
    try:
        identity = (directory/'root').stat()
        return (str((directory/'root').resolve(strict=True)), identity.st_dev,
                identity.st_ino, str((directory/'cwd').resolve(strict=True)))
    except OSError:
        return None


def delivery(pid, table, jobs, root, record, config_path, *, dependencies=None):
    """Verify native chroot rsync delivery; never relax upstream UID rules."""
    original = pid
    chain, seen = [], set()
    if dependencies is not None:
        dependencies.add(pid)
    child_uid = table[pid][2]
    root_daemon = False
    valid_credentials = True
    while pid:
        if pid in seen or len(seen) >= 128 or pid not in table:
            raise ValueError('delivery ancestry')
        seen.add(pid)
        if dependencies is not None:
            # A non-rsync ancestor terminates classification, not verification.
            dependencies.add(pid)
        parent, ticks, uid, args, status = table[pid]
        if not args or os.path.basename(args[0]) != 'rsync':
            break
        if status in ('Z', 'X', 'x') or uid not in (0, 65534):
            valid_credentials = False
        if chain and chain[-1][2] == 0 and uid != 0:
            valid_credentials = False
        chain.append([pid, ticks, uid])
        root_daemon |= uid == 0 and '--daemon' in args and pid != original
        pid = parent
    if not root_daemon:
        return set(), []
    directory = root/str(original)
    actual = str((directory/'root').resolve(strict=True))
    if actual == '/':
        # A proven daemon child without a selected chroot could belong to
        # any module. Unlike unrelated host sockets this is relevant loss
        # of coverage, and must not turn a known upstream zero into a total.
        return {job[0] for job in jobs}, []
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string('[global]\n' + bounded(config_path, 65536).decode())
    modules = set()
    for section in parser.sections():
        if section == 'global' or not parser.has_option(section, 'path'):
            continue
        if parser.get(section, 'use chroot', fallback=parser.get('global', 'use chroot', fallback='yes')) != 'yes':
            continue
        modules.add(str(Path(parser.get(section, 'path')).resolve(strict=True)))
    matches = {rid for rid, script, uid, roots in jobs if actual in roots}
    if len(matches) != 1 or actual not in modules or not valid_credentials:
        return matches, []
    cwd = str((directory/'cwd').resolve(strict=True))
    if cwd != actual and not cwd.startswith(actual + '/'):
        return matches, []
    inodes = re.findall(r'\bino:(\d+)\b', record)
    fds = re.findall(r'pid=' + str(original) + r',fd=(\d+)', record)
    if len(inodes) != 1 or not fds or any(os.readlink(directory/'fd'/fd) != 'socket:['+inodes[0]+']' for fd in fds):
        return matches, []
    socket_uids = re.findall(r'\buid:(\d+)\b', record)
    # ss suppresses UID 0. Only this proven root-daemon branch permits it.
    if socket_uids and (len(socket_uids) != 1 or int(socket_uids[0]) not in (0, child_uid)):
        return matches, []
    identity = (directory/'root').stat()
    canonical = Path(actual).stat()
    if (identity.st_dev, identity.st_ino) != (canonical.st_dev, canonical.st_ino):
        return matches, []
    chain[0].extend([identity.st_dev, identity.st_ino])
    return matches, chain


def sample(config, previous, *, now, proc_root: str | Path = '/proc', ss_reader=None,
           rsync_config: str | Path = '/etc/rsyncd.conf', diag_reader=None):

    """Return ``(rows, state)``; existing sync_* keys include rsync delivery.

    rows[rid] has sync_rx_bps, sync_tx_bps, rates_observed_at (epoch seconds),
    each None when unknown. ``now=None`` timestamps the actual observation;
    explicit now is only an epoch timestamp override, NEVER the elapsed clock.
    Delta timing always uses time.monotonic(), including test invocations.

    Production (ss_reader=None) requires read_diag and read_ss, /proc access
    and /etc/rsyncd.conf for daemon attribution. Tests may inject ss_reader
    (legacy explicit-counter-only mode; no empty zeros), or ss_reader plus
    diag_reader (production semantics with synthetic diagnostics). diag_reader
    returns the sanitized dict list produced by read_diag; proc_root and
    rsync_config redirect filesystem fixtures. Patch time.monotonic for tests.

    State version 2 has boot, at, monotonic, netns, direct, repositories.
    A repository's empty dict means a complete observed-empty baseline; missing
    means unknown. Legacy state, changed mode/boot/netns, or missing monotonic
    primes a fresh baseline and returns null, never migrates epoch to elapsed.
    Socket owners are numeric [pid,startticks,uid] lists; delivery holders also
    carry root device/inode. Callers must treat this private state as opaque.
    """
    rows = {rid: dict(sync_rx_bps=None, sync_tx_bps=None, rates_observed_at=None)
            for rid in config['repositories']}
    root = Path(proc_root)
    direct = ss_reader is None or diag_reader is not None
    started = time.monotonic() if direct else 0.0
    try:
        boot = uuid.UUID(bounded(root/'sys/kernel/random/boot_id', 128).decode().strip()).int
        table = processes(root)
        jobs = [(rid, j['script'], pwd.getpwnam(j['user']).pw_uid,
                 {os.path.normpath(j['path']), os.path.realpath(j['path'])})
                for rid, j in config['repositories'].items()]
        roots_before = {pid: root_identity(root/str(pid)) for pid, item in table.items()
                        if item[3] and os.path.basename(item[3][0]) == 'rsync'}
        records = parse_ss((ss_reader or read_ss)())
        namespace = None
        if direct:
            namespace = os.readlink(root/'self/ns/net')
            diagnostics = (diag_reader or read_diag)()
            if len(diagnostics) > MAX_SOCKETS:
                raise ValueError('socket count bound')
            by_key = {}
            for item in diagnostics:
                key = (item['ino'], item['cookie'])
                if key in by_key:
                    raise ValueError('duplicate diagnostic identity')
                by_key[key] = item
            joined, seen = [], set()
            for record in records:
                inos = re.findall(r'\bino:(\d+)\b', record)
                cookies = re.findall(r'\bsk:([0-9a-fA-F]+)\b', record)
                item = None
                if len(inos) == len(cookies) == 1:
                    key = (int(inos[0]), int(cookies[0], 16))
                    item = by_key.get(key)
                    seen.add(key)
                record = re.sub(r'\b(?:bytes_sent|bytes_received|uid):\d+\b', '', record)
                if item is not None:
                    record += ' uid:' + str(item['uid'])
                    if item['rx'] is not None and item['tx'] is not None:
                        record += ' bytes_received:' + str(item['rx']) + ' bytes_sent:' + str(item['tx'])
                joined.append(record)
            for key, item in by_key.items():
                if key not in seen and item['state'] != 10:
                    joined.append('UNKNOWN 0 0 - - ino:%s sk:%x' % key)
            records = joined
        # Production uses the actual observation time, not collector startup.
        # An explicit timestamp keeps isolated fixtures deterministic.
        now = time.time() if now is None else now
        monotonic = time.monotonic()
        sockets = {rid: {} for rid in rows}
        invalid = set()
        cookie_repos = {}
        uncertain_cookies = set()
        checked = set()
        deliveries = []
        held = []
        empty_complete = direct
        for record in records:
            if record.startswith('LISTEN '):
                continue
            pids = set(map(int, re.findall(r'\bpid=(\d+)', record)))
            if not pids and not record.startswith(('TIME-WAIT ', 'FIN-WAIT-1 ', 'FIN-WAIT-2 ')):
                empty_complete = False
            owners, identities = set(), []
            unattributed = False
            socket_uids = re.findall(r'\buid:(\d+)\b', record)
            for pid in sorted(pids):
                # Verification dependencies include excluded holders/ancestors;
                # they are deliberately separate from persisted socket owners.
                matches, chain = attribution(pid, table, jobs, dependencies=checked)
                serving = False
                if not matches:
                    matches, chain = delivery(pid, table, jobs, root, record, rsync_config,
                                              dependencies=checked)
                    serving = bool(matches and chain)
                    if serving and (roots_before.get(pid) is None or roots_before[pid] != root_identity(root/str(pid))):
                        chain = []
                        serving = False
                    if serving:
                        deliveries.append((pid, record, matches, chain))
                unattributed |= not matches or not chain
                if not serving and socket_uids and (len(socket_uids) != 1 or int(socket_uids[0]) != table[pid][2]):
                    unattributed = True
                if not matches and table[pid][3] and os.path.basename(table[pid][3][0]) in ('rsync', 'rsync-ssl', 'openssl', 'ssh'):
                    empty_complete = False
                if direct and matches:
                    inodes = re.findall(r'\bino:(\d+)\b', record)
                    fds = re.findall(r'pid=' + str(pid) + r',fd=(\d+)', record)
                    if len(inodes) != 1 or not fds or os.readlink(root/str(pid)/'ns/net') != namespace:
                        unattributed = True
                    else:
                        for fd in fds:
                            path = root/str(pid)/'fd'/fd
                            target = 'socket:['+inodes[0]+']'
                            if os.readlink(path) != target:
                                unattributed = True
                            held.append((pid, path, target))
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
        for pid, path, target in held:
            if os.readlink(path) != target or os.readlink(root/str(pid)/'ns/net') != namespace:
                raise ValueError('socket holder changed')
        if direct and (os.readlink(root/'self/ns/net') != namespace or time.monotonic()-started > MAX_SAMPLE_SECONDS):
            raise ValueError('sampling window or namespace changed')
        for pid, record, matches, chain in deliveries:
            if roots_before[pid] != root_identity(root/str(pid)) or delivery(pid, table, jobs, root, record, rsync_config) != (matches, chain):
                raise ValueError('delivery changed during sampling')
        for pid in checked:
            current_process = process(root/str(pid))
            if current_process[:4] != table[pid][:4] or current_process[4] in ('Z', 'X', 'x'):
                raise ValueError('ownership changed during sampling')
        if uuid.UUID(bounded(root/'sys/kernel/random/boot_id', 128).decode().strip()).int != boot:
            raise ValueError('boot changed')
        if direct and time.monotonic()-started > MAX_SAMPLE_SECONDS:
            raise ValueError('sampling window exceeded during verification')
        state = {'version': 2, 'boot': boot, 'at': now, 'monotonic': monotonic,
                 'netns': int(namespace[5:-1]) if namespace else None, 'direct': direct,
                 'repositories': {r: s for r, s in sockets.items() if (s or empty_complete) and r not in invalid}}
        if not isinstance(previous, dict) or previous.get('boot') != boot or previous.get('version') != 2 or previous.get('direct') != direct or previous.get('netns') != state['netns']:
            return rows, state
        old_at = previous.get('monotonic')
        if not isinstance(old_at, (int, float)) or isinstance(old_at, bool) or not 0 <= old_at <= MAX_COUNTER or not math.isfinite(old_at) or not 0 < monotonic-old_at <= MAX_SAMPLE_SECONDS:
            return rows, state
        dt = monotonic-old_at
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
    except (OSError, ValueError, UnicodeError, IndexError, KeyError, configparser.Error, subprocess.SubprocessError):
        return rows, {}
