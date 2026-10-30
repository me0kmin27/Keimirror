"""Transient namespace-proved RPC exclusion. No endpoint is persisted."""
import os
from pathlib import Path
import time
import ipaddress
import json
import math
import secrets
import socket
import stat
import struct
import uuid
import ctypes

if __package__:
    from .rates import bounded, ObservationChurn, observation_budget
    from .private_reasons import Reason, emit
else:  # Existing /opt/keimirror-telemetry flat installation.
    from rates import bounded, ObservationChurn, observation_budget
    from private_reasons import Reason, emit

ROOT_UID = 0
SOCKET_PATH = '/run/keimirror-rpc/rpc.sock'
MAX_MESSAGE = 131072


def protect_memory():
    """RLIMIT_CORE alone does not cover piped core handlers."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                          ctypes.c_ulong, ctypes.c_ulong]
    if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
        raise OSError('RPC core protection failed')


def identity():
    return (uuid.UUID(bounded('/proc/sys/kernel/random/boot_id', 128).decode().strip()).int,
            os.readlink('/proc/self/ns/net'))


def peer_identity(conn):
    pid, uid, gid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != ROOT_UID or pid <= 0:
        raise ValueError('RPC peer credentials')
    ticks = bounded('/proc/%s/stat' % pid, 8192).decode().rsplit(')', 1)[1].split()[19]
    netns = os.readlink('/proc/%s/ns/net' % pid)
    if netns != os.readlink('/proc/self/ns/net'):
        raise ValueError('RPC peer namespace')
    return pid, ticks, netns


def receive(conn, limit):
    data, anc, flags, addr = conn.recvmsg(limit)
    if not data or flags & socket.MSG_TRUNC or anc:
        raise ValueError('RPC message bound')
    return data


def validate_reply(reply, nonce, boot, netns, start, finish):
    if (not isinstance(reply, dict) or set(reply) !=
            {'nonce', 'boot', 'netns', 'started', 'finished', 'transports'}):
        raise ValueError('RPC reply shape')
    a, b = reply['started'], reply['finished']
    if (any(type(t) not in (float, int) or not math.isfinite(t) for t in (a, b))
            or not start <= a <= b <= finish or not 0 < finish-start <= 2
            or (reply['nonce'], reply['boot'], reply['netns']) != (nonce, boot, netns)):
        raise ValueError('RPC reply identity or freshness')
    rows = reply['transports']
    if not isinstance(rows, list) or len(rows) > 512:
        raise ValueError('RPC rows')
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'endpoint', 'inode', 'connected'}
                or type(row['inode']) is not int or row['inode'] <= 0
                or type(row['connected']) is not bool):
            raise ValueError('RPC row')
        endpoint = row['endpoint']
        if (not isinstance(endpoint, list) or len(endpoint) != 5 or endpoint[0] not in (2, 10)
                or any(type(p) is not int or not 0 < p < 65536 for p in endpoint[3:])):
            raise ValueError('RPC endpoint')
        width = 8 if endpoint[0] == 2 else 32
        for address in endpoint[1:3]:
            if not isinstance(address, str) or len(address) != width or bytes.fromhex(address).hex() != address:
                raise ValueError('RPC address')
    return rows


def read_snapshot(path: str | Path = SOCKET_PATH):
    """Root-only peer authenticated, nonce-bound, no cached response."""
    start = time.monotonic()
    observation_budget()
    path = Path(path)
    parent, node = path.parent.lstat(), path.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != ROOT_UID
            or stat.S_IMODE(parent.st_mode) != 0o700 or not stat.S_ISSOCK(node.st_mode)
            or node.st_uid != ROOT_UID or stat.S_IMODE(node.st_mode) != 0o600):
        raise ValueError('RPC IPC permissions')
    boot, netns = identity()
    nonce = secrets.token_hex(16)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
        conn.settimeout(observation_budget(0.5))
        conn.connect(str(path))
        peer = peer_identity(conn)
        conn.settimeout(observation_budget(0.5))
        conn.sendall(nonce.encode('ascii'))
        conn.settimeout(observation_budget(0.5))
        reply = json.loads(receive(conn, MAX_MESSAGE))
        if peer_identity(conn) != peer or identity() != (boot, netns):
            raise ValueError('RPC identity changed')
    return validate_reply(reply, nonce, boot, netns, start, time.monotonic())


def respond(conn, reader):
    """Exactly one bounded request. Caller mounts/drops caps before serving."""
    conn.settimeout(0.5)
    peer = peer_identity(conn)
    nonce = receive(conn, 32).decode('ascii')
    if len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce):
        raise ValueError('RPC nonce')
    boot, netns = identity()
    start = time.monotonic()
    transports = reader()
    finish = time.monotonic()
    if peer_identity(conn) != peer or identity() != (boot, netns) or not 0 <= finish-start <= 2:
        raise ValueError('RPC serving identity or window')
    data = json.dumps(dict(nonce=nonce, boot=boot, netns=netns, started=start,
                           finished=finish, transports=transports), allow_nan=False).encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError('RPC output bound')
    conn.sendall(data)


def read_transports(root):
    """Read only the freshly mounted namespace-bound tree supplied by helper."""

    base = Path(root)/'kernel/sunrpc/xprt-switches'
    base.stat()  # Missing sunrpc is unavailable, never an inherited /sys fallback.
    result, count = [], 0
    def failed(error):
        raise error
    for directory, dirs, files in os.walk(base, followlinks=False, onerror=failed):
        path = Path(directory)
        count += len(dirs) + len(files)
        if count > 512 or len(path.relative_to(base).parts) > 3:
            raise ValueError('RPC tree bound')
        if 'xprt_info' not in files or not path.name.endswith('-tcp'):
            continue
        inode = path.stat().st_ino
        src = ipaddress.ip_address(bounded(path/'srcaddr', 256).decode().strip())
        dst = ipaddress.ip_address(bounded(path/'dstaddr', 256).decode().strip())
        if src.version != dst.version or src.is_link_local or dst.is_link_local:
            raise ValueError('RPC family or unsupported scope')
        fields = {}
        for line in bounded(path/'xprt_info', 8192).decode().splitlines():
            key, value = line.split('=', 1)
            if key in fields:
                raise ValueError('duplicate RPC field')
            fields[key] = value
        ports = [int(fields[n]) for n in ('src_port', 'dst_port')]
        if not all(0 < port < 65536 for port in ports):
            raise ValueError('RPC port')
        connected = 'CONNECTED' in bounded(path/'xprt_state', 1024).decode().split()
        if path.stat().st_ino != inode:
            raise ValueError('RPC transport changed')
        result.append(dict(endpoint=[2 if src.version == 4 else 10,
                                     src.packed.hex(), dst.packed.hex(), *ports],
                           inode=inode, connected=connected))
    return result


def holders(root):
    """A missing/unreadable process or FD is a coverage gap, not ownerless."""
    try:
        return _holders(root)
    except FileNotFoundError as exc:
        # The sampler must restart process/ss/diag/RPC acquisition together.
        # A standalone proof caller still receives an OSError, never a subset.
        raise ObservationChurn(exc.errno, 'incomplete proc inventory') from exc


def _holders(root):
    found = set()
    count = 0
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            observation_budget()
            if index >= 16384:
                raise ValueError('process bound')
            if not entry.name.isdecimal():
                continue
            try:
                fds = os.scandir(Path(entry.path)/'fd')
            except FileNotFoundError:
                emit(Reason.TCP_HOLDER_DIR_ENOENT)
                raise
            with fds:
                for fd in fds:
                    observation_budget()
                    count += 1
                    if count > 131072:
                        raise ValueError('descriptor bound')
                    try:
                        target = os.readlink(fd.path)
                    except FileNotFoundError:
                        emit(Reason.TCP_HOLDER_LINK_ENOENT)
                        raise
                    if target.startswith('socket:[') and target.endswith(']'):
                        found.add(int(target[8:-1]))
    return found


def exclusions(first, diag_reader, rpc_reader, root):
    """Two fresh RPC/diag/holder observations, bounded by sampler deadline."""
    start = time.monotonic()
    observation_budget()
    rpc_a = rpc_reader()
    observation_budget()
    try:
        held_a = holders(root)
    except FileNotFoundError:
        emit(Reason.TCP_RPC_FIRST_HOLDERS_ENOENT)
        raise
    observation_budget()
    rpc_b = rpc_reader()
    observation_budget()
    second = diag_reader()
    observation_budget()
    try:
        held_b = holders(root)
    except FileNotFoundError:
        emit(Reason.TCP_RPC_SECOND_HOLDERS_ENOENT)
        raise
    observation_budget()
    if not 0 < time.monotonic() - start <= 2:
        raise ValueError('RPC window')
    result = set()
    for a in first:
        endpoint = a.get('endpoint')
        b = [x for x in second if x.get('endpoint') == endpoint]
        r = [x for x in rpc_a if x.get('endpoint') == endpoint]
        s = [x for x in rpc_b if x.get('endpoint') == endpoint]
        identity = ('ino', 'cookie', 'uid', 'state', 'endpoint')
        if (endpoint is not None and len(b) == len(r) == len(s) == 1
                and all(a[k] == b[0][k] for k in identity)
                and r[0] == s[0] and r[0]['connected'] is True
                and r[0]['inode'] > 0 and a['ino'] > 0
                and a['cookie'] not in (0, 2**64-1) and a['uid'] == 0
                and a['state'] == 1 and a['ino'] not in held_a | held_b):
            result.add((a['ino'], a['cookie']))
    return result
