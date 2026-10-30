"""Separate root-only setup service; collector NEVER imports mount operations.

No external input is processed until a private fresh sysfs mount is verified and
CAP_SYS_ADMIN (including bounding/ambient sets) is irrevocably removed. The only
remaining capabilities are the collector's proc-visibility capabilities.
CAP_SYS_PTRACE is powerful, not intrinsically read-only: keep this fixed-path
service root-owned and never extend the IPC to accept commands or paths.
"""
import ctypes
import os
from pathlib import Path
import re
import signal
import socket
import stat

if __package__:
    from .rates import bounded
    from .rpc import read_transports, respond, protect_memory
else:
    from rates import bounded
    from rpc import read_transports, respond, protect_memory

READ_CAPS = (1 << 19) | (1 << 2)
RUNTIME = Path('/run/keimirror-rpc')


def verify_mount(target, text):
    rows = []
    for line in text.splitlines():
        left, right = line.split(' - ', 1)
        a, b = left.split(), right.split()
        decode = lambda value: re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
        if len(a) < 6 or len(b) < 3:
            raise ValueError('mountinfo shape')
        if os.path.normpath(decode(a[4])) == os.path.normpath(target):
            rows.append((a, b))
    if len(rows) != 1:
        raise ValueError('fresh mount missing or ambiguous')
    a, b = rows[0]
    flags = set(a[5].split(','))
    if (a[3] != '/' or b[0] != 'sysfs' or not {'ro', 'nosuid', 'nodev', 'noexec'} <= flags
            or 'rw' in flags or any(x.startswith(('shared:', 'master:', 'propagate_from:')) for x in a[6:])):
        raise ValueError('fresh mount isolation or flags')
    # Deliberately do not demand superblock ro: never remount a shared superblock.


class Linux:
    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_ulong, ctypes.c_void_p]
        self.libc.mount.restype = ctypes.c_int
        self.libc.unshare.argtypes = [ctypes.c_int]
        self.libc.unshare.restype = ctypes.c_int
        self.libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                   ctypes.c_ulong, ctypes.c_ulong]
        self.libc.prctl.restype = ctypes.c_int

    def check(self, value):
        if value != 0:
            raise OSError(ctypes.get_errno(), 'RPC setup syscall failed')

    def namespaces(self):
        return {name: os.stat('/proc/self/ns/'+name).st_ino for name in ('mnt', 'net', 'user')}

    def unshare(self):
        self.check(self.libc.unshare(0x00020000))  # CLONE_NEWNS only

    def private(self):
        self.check(self.libc.mount(None, b'/', None, (1 << 18) | (1 << 14), None))

    def mount(self, target):
        self.check(self.libc.mount(b'sysfs', os.fsencode(target), b'sysfs', 1 | 2 | 4 | 8, None))

    def mountinfo(self):
        return bounded('/proc/self/mountinfo', 1048576).decode()

    def drop(self):
        class Header(ctypes.Structure):
            _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
        class Data(ctypes.Structure):
            _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                        ('inheritable', ctypes.c_uint32)]
        last = int(bounded('/proc/sys/kernel/cap_last_cap', 32))
        if not 0 <= last < 64:
            raise ValueError('capability ABI')
        for cap in range(last + 1):
            self.check(self.libc.prctl(24, cap, 0, 0, 0))  # bounding drop
        self.check(self.libc.prctl(47, 4, 0, 0, 0))  # clear ambient
        self.check(self.libc.prctl(38, 1, 0, 0, 0))  # no_new_privs
        header = Header(0x20080522, 0)
        data = (Data * 2)(Data(READ_CAPS, READ_CAPS, 0), Data(0, 0, 0))
        self.check(self.libc.capset(ctypes.byref(header), ctypes.byref(data)))

    def verify_caps(self):
        verify_caps(bounded('/proc/self/status', 16384).decode())


def verify_caps(text):
    fields = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
    if (any(int(fields[k], 16) != READ_CAPS for k in ('CapEff', 'CapPrm'))
            or any(int(fields[k], 16) != 0 for k in ('CapInh', 'CapAmb', 'CapBnd'))
            or fields['NoNewPrivs'].strip() != '1'):
        raise ValueError('RPC capabilities not dropped')


def prepare(target, *, kernel=None):
    """One-shot setup; any failure exits service, never uses inherited /sys."""
    kernel = Linux() if kernel is None else kernel
    target = Path(target)
    target.mkdir(mode=0o700)  # Must not preexist or be a symlink.
    before = kernel.namespaces()
    kernel.unshare()
    after = kernel.namespaces()
    if after['mnt'] == before['mnt'] or any(after[n] != before[n] for n in ('net', 'user')):
        raise ValueError('RPC namespace isolation')
    kernel.private()
    kernel.mount(target)
    verify_mount(str(target), kernel.mountinfo())
    if kernel.namespaces() != after:
        raise ValueError('RPC namespace changed during mount')
    kernel.drop()
    kernel.verify_caps()
    protect_memory()  # Capability changes may reset dumpability.
    return after


def serve(target, namespaces, kernel):
    node = RUNTIME.lstat()
    if not stat.S_ISDIR(node.st_mode) or node.st_uid != 0 or stat.S_IMODE(node.st_mode) != 0o700:
        raise ValueError('RPC runtime permissions')
    # No unlink/reuse of a pre-existing socket. systemd owns runtime cleanup.
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
        listener.bind(str(RUNTIME/'rpc.sock'))
        os.chmod(RUNTIME/'rpc.sock', 0o600)
        listener.listen(4)
        def fresh():
            kernel.verify_caps()
            if kernel.namespaces() != namespaces:
                raise ValueError('RPC namespace changed')
            verify_mount(str(target), kernel.mountinfo())
            return read_transports(target)
        while True:
            conn, _ = listener.accept()
            with conn:
                signal.alarm(2)  # A wedged kernel/sysfs read kills helper, not collector.
                try:
                    respond(conn, fresh)
                except (OSError, ValueError, KeyError, UnicodeError):
                    pass  # No endpoints, paths or exception text in journal.
                finally:
                    signal.alarm(0)


def main():
    if os.geteuid() != 0:
        return 1
    os.umask(0o077)
    signal.alarm(10)
    try:
        protect_memory()
        kernel = Linux()
        target = RUNTIME/'sysfs'
        namespaces = prepare(target, kernel=kernel)
        signal.alarm(0)
        serve(target, namespaces, kernel)
    except (OSError, ValueError, KeyError):
        return 1
    finally:
        signal.alarm(0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
