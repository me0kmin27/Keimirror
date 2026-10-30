"""Root-only, same-boot nginx module re-exec transaction. Review required."""
import argparse
import fcntl
import glob
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

OLD = '700adce1c053f3a0b1accf590f77d1f79d68a574599b47025af65b0dea0d02f1'
NEW = 'a5035e055ece858073606d8e4952167286914eafb8704e2cfa29a2bc97e247e5'
PACKAGE = 'a057348974aa3ab2ae5297e02ddaac7b882d4c4534c285e2d6f649e69ca6ea4e'
BINARY = '3f804ae7dda33a8368993d5d6087bfbc5fbf8c8ed1760af12fc9e8a74966d8a7'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read(path, limit=16 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('bounded regular file required')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if len(data) > limit or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('file changed or bound exceeded')
        return bytes(data)
    finally:
        os.close(fd)


def snapshot(path):
    p = Path(path)
    s = p.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('regular file required')
    return dict(sha256=digest(read(p)), uid=s.st_uid, gid=s.st_gid,
                mode=stat.S_IMODE(s.st_mode), mtime_ns=s.st_mtime_ns)


def syncdir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, data, metadata=None, exclusive=False):
    p = Path(path)
    fd, name = tempfile.mkstemp(prefix='.reexec-', dir=p.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), metadata['mode'] if metadata else 0o600)
            if metadata:
                os.fchown(f.fileno(), metadata['uid'], metadata['gid'])
                os.utime(f.fileno(), ns=(metadata['mtime_ns'], metadata['mtime_ns']))
            os.fsync(f.fileno())
        if exclusive:
            os.link(name, p, follow_symlinks=False)
            os.unlink(name)
        else:
            os.replace(name, p)
        syncdir(p.parent)
        if read(p) != data:
            raise ValueError('atomic readback failed')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def trusted(path, private=False):
    p = Path(path)
    if not p.is_absolute():
        raise ValueError('absolute path required')
    for item in [*reversed(p.parents), p]:
        s = item.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid or s.st_gid or s.st_mode & 0o002:
            raise ValueError('untrusted root path')
    if private and p.stat().st_mode & 0o077:
        raise ValueError('private path required')
    return p


def identity_snapshot(path):
    """Freeze immutable identity as well as restorable metadata; never follow leaf links."""
    p = Path(path)
    before = p.lstat()
    value = snapshot(p)
    after = p.lstat()
    fields = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, k) != getattr(after, k) for k in fields):
        raise ValueError('configuration changed during snapshot')
    return dict(value, dev=after.st_dev, ino=after.st_ino)


def readonly_parents(path, frozen):
    """Pin an existing config's ancestor authority, never an installer write path."""
    p = Path(path)
    if 'parents' not in frozen:
        trusted(p.parent)
        return
    if not p.is_absolute() or set(frozen['parents']) != {str(a) for a in p.parents}:
        raise ValueError('exact readonly parent chain required')
    for a in p.parents:
        s = a.lstat()
        actual = dict(uid=s.st_uid, gid=s.st_gid, mode=stat.S_IMODE(s.st_mode),
                      dev=s.st_dev, ino=s.st_ino)
        if not stat.S_ISDIR(s.st_mode) or s.st_mode & 0o002 or actual != frozen['parents'][str(a)]:
            raise ValueError('readonly directory identity/owner/mode drift')


def readonly_config(path, allowlist):
    """A narrow existing-config exception, NEVER usable for a write/execution input.

    Parent directories are root controlled or individually pinned in the same
    approved readonly policy. Existing operator authority is preserved, not granted: exact path, bytes, owner, permissions and inode must
    match the separately approved inventory. No chmod/chown/write is performed.
    """
    p = Path(path)
    if str(p) not in allowlist:
        trusted(p)
        return identity_snapshot(p)
    expected = allowlist[str(p)]
    readonly_parents(p, expected)
    actual = identity_snapshot(p)
    readonly_parents(p, expected)
    if actual['mode'] & 0o002 or actual != {k: v for k, v in expected.items() if k != 'parents'}:
        raise ValueError('readonly configuration identity/owner/hash drift')
    return actual


def config_inventory(config, allowlist=None, links=None):
    """Read includes only; never nginx -T (it can open application log files).

    Unsupported QUIC is rejected even with quic_bpf on: this transaction does
    not yet prove BPF ownership across generations. Never silently ignore it.
    Nonzero shutdown policy is not changed: fail rather than promise no kills.
    """
    files, directives = {}, []
    base = Path(config).parent
    def visit(path):
        lexical = Path(path)
        if links is not None:
            for part in [*reversed(lexical.parents), lexical]:
                s = part.lstat()
                if stat.S_ISLNK(s.st_mode):
                    link = dict(target=os.readlink(part), dev=s.st_dev,
                                ino=s.st_ino, uid=s.st_uid, gid=s.st_gid,
                                mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)
                    if allowlist is not None:
                        if str(part) in allowlist:
                            expected = allowlist[str(part)]
                            readonly_parents(part, expected)
                            if link != {k: v for k, v in expected.items() if k != 'parents'}:
                                raise ValueError('readonly symlink identity drift')
                        else:
                            trusted(part.parent)
                            if s.st_uid or s.st_gid:
                                raise ValueError('non-root configuration link')
                    links[str(part)] = link
        path = lexical.resolve(strict=True)
        if allowlist is not None:
            readonly_config(path, allowlist)
        if str(path) in files:
            return
        if len(files) >= 256:
            raise ValueError('configuration inventory bound')
        files[str(path)] = snapshot(path)
        lexer = shlex.shlex(read(path).decode(), posix=True, punctuation_chars=';{}')
        lexer.whitespace_split = True
        tokens = []
        for token in lexer:
            if token and all(c in ';{}' for c in token):
                if tokens:
                    directives.append(tokens)
                    if tokens[0] == 'include':
                        if len(tokens) != 2 or '$' in tokens[1]:
                            raise ValueError('unsupported include')
                        pattern = tokens[1] if tokens[1].startswith('/') else str(base / tokens[1])
                        matches = sorted(glob.glob(pattern))
                        if not matches and not glob.has_magic(pattern):
                            raise ValueError('missing include')
                        for match in matches:
                            visit(match)
                tokens = []
            else:
                tokens.append(token)
        if tokens:
            raise ValueError('unterminated configuration')
    visit(config)
    if any(d[0] == 'listen' and ('quic' in d or 'http3' in d) for d in directives):
        raise ValueError('QUIC listeners unsupported: quic_bpf migration proof required')
    if any(d[0] == 'worker_shutdown_timeout' and d[1:] not in (['0'], ['0s'], ['0ms']) for d in directives):
        raise ValueError('nonzero worker_shutdown_timeout forcibly closes clients; policy unchanged')
    return files


BUDGETS = dict(copy=30, syntax=15, workers=60, acceptance=30, drain=120, adoption=15)
TOTAL = 270
RECOVERY = 180
# Absolute monotonic deadlines survive same-boot resume. Recovery cannot extend
# the hard ceiling, nor can unused forward time enlarge its own budget.
ACTIVE_DEADLINE = None


def deadline_expired(signum, frame):
    raise TimeoutError('executor deadline expired; nginx clients are retained')


class Deadlines:
    def __init__(self, journal, save):
        self.journal, self.save = journal, save
        if 'budget' not in journal:
            now = time.monotonic()
            journal['budget'] = dict(start=now, forward_end=now + TOTAL,
                                     hard_end=now + TOTAL + RECOVERY)
            save(journal)
        self.budget = dict(journal['budget'])
        self.hard_end = self.budget['hard_end']
        self.deadline = self.budget.get('recovery_end', self.budget['forward_end'] if journal.get('phase', 'prepared') == 'prepared' else self.hard_end)
        signal.signal(signal.SIGALRM, deadline_expired)
        self.arm()

    def arm(self):
        global ACTIVE_DEADLINE
        ACTIVE_DEADLINE = self.deadline
        signal.setitimer(signal.ITIMER_REAL, self.remaining())

    def phase(self, name):
        if name == 'recovery':
            if 'recovery_end' not in self.budget:
                self.budget['recovery_end'] = min(self.hard_end, time.monotonic() + RECOVERY)
                self.journal['budget'] = dict(self.budget)
                self.save(self.journal)
            self.deadline = self.budget['recovery_end']
        else:
            if 'recovery_end' in self.budget:
                raise ValueError('recovery has priority; forward execution cannot resume')
            self.deadline = min(self.budget['forward_end'], time.monotonic() + BUDGETS[name])
        self.arm()

    def remaining(self):
        left = min(self.deadline, self.hard_end) - time.monotonic()
        if left <= 0:
            raise TimeoutError('absolute deadline; clients are retained, never killed')
        return left


def process(pid):
    root = Path('/proc') / str(pid)
    raw = read(root / 'stat', 8192).decode()
    fields = raw.rsplit(')', 1)[1].split()
    if fields[0] in ('Z', 'X'):
        raise ProcessLookupError('dead process')
    return dict(identity=[pid, int(fields[19])], parent=int(fields[1]),
                title=read(root / 'cmdline', 8192).rstrip(b'\0').decode(),
                uid=root.stat().st_uid,
                cgroup=read(root / 'cgroup', 8192).decode(),
                exe=os.readlink(root / 'exe'))


def mapping_matches(pid, path, sha):
    s = Path(path).stat()
    if digest(read(path)) != sha:
        return False
    rows = [line.split(maxsplit=5) for line in read(f'/proc/{pid}/maps').decode().splitlines()]
    matched = [row for row in rows if len(row) == 6 and row[5] == str(path)]
    return bool(matched) and all(int(row[4]) == s.st_ino and
        tuple(int(x, 16) for x in row[3].split(':')) == (os.major(s.st_dev), os.minor(s.st_dev))
        for row in matched)


def signal_identity(identity, name):
    """pidfd acquired BEFORE rechecking start identity closes PID reuse race."""
    fd = os.pidfd_open(identity[0])
    try:
        p = process(identity[0])
        if p['identity'] != identity or p['uid'] != 0 or not p['title'].startswith('nginx: master process '):
            raise ValueError('master PID identity mismatch')
        signal.pidfd_send_signal(fd, getattr(signal, 'SIG' + name))
    finally:
        os.close(fd)


class Transaction:
    """Write-ahead phases: interruption resumes conservatively via rollback.

    No signal is repeated to an unverified PID. Commit intent is irreversible:
    once old QUIT might have happened, only reconcile adoption, never signal old.
    """
    def __init__(self, backend, journal):
        self.b, self.j = backend, dict(journal)

    def mark(self, phase):
        self.j['phase'] = phase
        self.b.save(self.j)

    def apply(self):
        if self.j['phase'] != 'prepared':
            raise ValueError('use rollback to reconcile an interrupted transaction')
        self.b.preflight()
        try:
            self.b.phase('copy')
            self.mark('install-intent')
            self.b.install()
            self.b.phase('syntax')
            self.b.syntax()
            self.mark('usr2-intent')
            self.b.signal(self.j['old'], 'USR2')
            self.b.phase('workers')
            self.j['new'] = self.b.discover()
            self.mark('new-discovered')
            self.b.ready(self.j['new'])
            self.mark('winch-intent')
            self.b.signal(self.j['old'], 'WINCH')
            self.b.phase('acceptance')
            self.b.accept()
            self.b.phase('drain')
            self.b.drain(self.j['old'])
            self.mark('commit-intent')
            self.b.signal(self.j['old'], 'QUIT')
            self.b.phase('adoption')
            self.b.adopted(self.j['new'])
            self.b.finish()
            self.mark('committed')
        except Exception:
            if self.j['phase'] != 'commit-intent':
                self.rollback()
            raise

    def rollback(self):
        if self.j['phase'] in ('commit-intent', 'committed'):
            raise ValueError('old generation commit is irreversible; reconcile only. A legacy-module switch requires a separately reviewed new upgrade, never HUP of the retired master')
        if self.j['phase'] == 'rolled-back':
            return
        signaled = self.j['phase'] not in ('prepared', 'install-intent', 'restore-files-intent')
        resumed_quit = self.j['phase'] == 'new-quit-intent'
        self.b.phase('recovery')
        if signaled and self.j['new'] is None:
            self.j['new'] = self.b.discover_optional()
            if self.j['new'] is None:
                raise ValueError('ambiguous USR2 intent without published new master; preserve generations for reconciliation')
        if not resumed_quit:
            self.mark('restore-intent' if signaled else 'restore-files-intent')
        self.b.restore()
        self.b.syntax()
        if signaled:
            if not resumed_quit:
                self.mark('old-hup-intent')
                self.b.signal(self.j['old'], 'HUP')
            self.b.ready(self.j['old'])
            if self.j['new'] is not None:
                self.mark('new-quit-intent')
                self.b.signal(self.j['new'], 'QUIT')
                self.b.drain(self.j['new'])
            self.b.adopted(self.j['old'])
        self.b.finish()
        self.mark('rolled-back')


def command(argv, timeout=10, data=None):
    if ACTIVE_DEADLINE is not None:
        timeout = min(timeout, ACTIVE_DEADLINE - time.monotonic())
        if timeout <= 0:
            raise TimeoutError('command absolute deadline')
    return subprocess.run(argv, input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, timeout=timeout,
                          env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}).stdout


def unit_state(unit):
    fields = ('MainPID', 'ActiveState', 'SubState', 'Result', 'ControlGroup',
              'Type', 'PIDFile', 'GuessMainPID', 'NotifyAccess', 'KillMode',
              'Restart', 'TimeoutStopUSec', 'ExecStart', 'ExecStop', 'RuntimeDirectory')
    raw = command(['systemctl', 'show', unit, *['--property=' + x for x in fields]])
    return dict(line.split('=', 1) for line in raw.decode().splitlines())


def machine():
    return dict(hostname=os.uname().nodename, boot=read('/proc/sys/kernel/random/boot_id').decode().strip(),
                netns=os.readlink('/proc/self/ns/net'), machine=read('/etc/machine-id').decode().strip())


def packages():
    return digest(command(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n']))


def pidfile(path):
    trusted(path)
    raw = read(path, 32).strip()
    if not re.fullmatch(rb'[1-9][0-9]{0,9}', raw):
        raise ValueError('invalid pidfile')
    return int(raw)


def children(master):
    first = process(master[0])
    if first['identity'] != master:
        raise ValueError('master identity changed')
    path = f'/proc/{master[0]}/task/{master[0]}/children'
    pids = read(path, 16384).split()
    workers = []
    for raw in pids:
        p = process(int(raw))
        if p['parent'] != master[0] or p['exe'] != first['exe'] or p['cgroup'] != first['cgroup']:
            raise ValueError('child ancestry mismatch')
        if p['title'] in ('nginx: worker process', 'nginx: worker process is shutting down'):
            workers.append(p['identity'])
        elif p['title'] not in ('nginx: cache manager process', 'nginx: cache loader process') and not p['title'].startswith('nginx: master process '):
            raise ValueError('unknown child role')
    if pids != read(path, 16384).split() or process(master[0]) != first:
        raise ValueError('child inventory changed')
    return sorted(workers)


def sockets(pid):
    result = set()
    for fd in Path(f'/proc/{pid}/fd').iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        if target.startswith('socket:['):
            result.add(target[8:-1])
    return result


def listeners(pid):
    held = sockets(pid)
    result = set()
    for name in ('tcp', 'tcp6'):
        for row in read(f'/proc/{pid}/net/{name}', 4 * 1024 * 1024).decode().splitlines()[1:]:
            fields = row.split()
            if fields[3] == '0A' and fields[9] in held:
                result.add(fields[9])
    for name in ('udp', 'udp6'):
        for row in read(f'/proc/{pid}/net/{name}', 4 * 1024 * 1024).decode().splitlines()[1:]:
            if row.split()[9] in held:
                raise ValueError('UDP/QUIC listener unsupported')
    for row in read(f'/proc/{pid}/net/unix', 4 * 1024 * 1024).decode().splitlines()[1:]:
        f = row.split()
        if f[3] == '00010000' and f[6] in held:
            result.add(f[6])
    if not result:
        raise ValueError('no inherited listeners')
    return sorted(result)


def reader_sample(spec, previous):
    # Fresh interpreter: no persistent import cache or parent sys.path mutation.
    code = '''import sys,json,time
sys.path.insert(0,sys.argv[1])
import http_rates as h
s=json.load(sys.stdin)
h.PID_PATH=s['pid'];h.SOCKET_PATH=s['status']
raw=h.read_http(time.monotonic()+2)
rids=json.loads(raw)['repositories']
rows,state=h.sample({'repositories':rids},s['previous'])
print(json.dumps({'rows':rows,'state':state}))
'''
    payload = dict(pid=spec['pid'], status=spec['status'], previous=previous)
    return json.loads(command([sys.executable, '-I', '-S', '-B', '-c', code, spec['reader_dir']], 5, encoded(payload)))


def probe(spec, timeout=10):
    # curl enforces a wall-clock deadline, response cap, no redirects or proxies.
    args = ['curl', '--silent', '--show-error', '--noproxy', '*', '--max-time', str(timeout),
            '--max-filesize', '262144', '--output', '-', '--write-out', '\n%{http_code}']
    if 'range' in spec:
        args += ['--range', spec['range']]
    raw = command([*args, '--', spec['url']], timeout + 1)
    body, status_code = raw.rsplit(b'\n', 1)
    if int(status_code) != spec['code'] or len(body) > 262144:
        raise ValueError('HTTP probe failed')
    if 'size' in spec and (len(body) != spec['size'] or digest(body) != spec['sha256']):
        raise ValueError('download byte verification failed')
    return dict(code=int(status_code), size=len(body), sha256=digest(body))


def prepare(spec, stage):
    if os.geteuid() != 0:
        raise PermissionError('root required')
    trusted(Path(__file__).absolute())
    stage = Path(stage)
    trusted(stage.parent, private=True)
    if spec['hostname'] != os.uname().nodename or type(spec['workers']) is not int or not 1 <= spec['workers'] <= 1024:
        raise ValueError('host/worker binding mismatch')
    for key in ('binary', 'load', 'old_module', 'package'):
        trusted(spec[key])
    for key in ('new_module', 'pid', 'status'):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', spec[key]):
            raise ValueError('unsafe absolute path')
    new = Path(spec['new_module'])
    trusted(new.parent)
    if new.name != 'ngx_http_project_live_module-' + NEW + '.so' or new.exists() or new.is_symlink():
        raise ValueError('candidate must be new content-addressed path')
    if digest(read(spec['binary'])) != BINARY or digest(read(spec['old_module'])) != OLD or digest(read(spec['package'])) != PACKAGE:
        raise ValueError('reviewed artifact mismatch')
    original = read(spec['load'])
    if digest(original) != spec['load_sha256'] or original.strip() != ('load_module ' + spec['old_module'] + ';').encode():
        raise ValueError('sole reviewed absolute load directive required')
    target = ('load_module ' + spec['new_module'] + ';\n').encode()
    allowlist = spec.get('readonly_configs', {})
    if any(p in allowlist for p in (spec['load'], spec['binary'], spec['old_module'], spec['new_module'], spec['package'])):
        raise ValueError('readonly exception cannot authorize execution or mutation inputs')
    links = {}
    files = config_inventory(spec['config'], allowlist, links)
    if not set(allowlist) <= set(files) | set(links):
        raise ValueError('readonly exception must name an included existing config')
    if spec['load'] not in files:
        raise ValueError('load configuration not included')
    preserved = {path: readonly_config(path, allowlist) for path in files if path != spec['load']}
    # Reject a second project module load anywhere, not merely in this fragment.
    loads = re.findall(rb'\bload_module\s+([^;]+);', b'\n'.join(read(p) for p in files))
    if [x for x in loads if b'project_live' in x or x == spec['old_module'].encode()] != [spec['old_module'].encode()]:
        raise ValueError('duplicate project module load')
    backups = spec['retained_backups']
    if not backups:
        raise ValueError('retained backup gate missing')
    for path, sha in backups.items():
        if digest(read(trusted(path))) != sha:
            raise ValueError('retained backup mismatch')
    old = process(pidfile(spec['pid']))
    if old['uid'] or not old['title'].startswith('nginx: master process ') or old['exe'] != spec['binary']:
        raise ValueError('unrecognized old master')
    if spec['old_module'] not in read(f'/proc/{old["identity"][0]}/maps').decode():
        raise ValueError('old mapping missing')
    workers = children(old['identity'])
    if len(workers) != spec['workers']:
        raise ValueError('worker baseline mismatch')
    state = unit_state(spec['unit'])
    if state['Type'] != 'forking' or state['PIDFile'] != spec['pid'] or state['ActiveState'] != 'active' or int(state['MainPID']) != old['identity'][0] or state['NotifyAccess'] != 'none' or state['KillMode'] != 'mixed':
        raise ValueError('unreviewed systemd lifecycle')
    if state['ControlGroup'] not in old['cgroup']:
        raise ValueError('master cgroup mismatch')
    readers = {str(Path(spec['reader_dir']) / n): snapshot(trusted(Path(spec['reader_dir']) / n)) for n in ('http_rates.py', 'private_reasons.py')}
    initial = reader_sample(spec, {})
    if not initial['state']:
        raise ValueError('initial status not ready')
    if Path(spec['pid'] + '.oldbin').exists():
        raise ValueError('existing re-exec transaction')
    tar = command(['dpkg-deb', '--fsys-tarfile', spec['package']])
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        members = [x for x in archive.getmembers() if x.name.endswith('/ngx_http_project_live_module.so') and x.isfile()]
        if len(members) != 1 or members[0].size > 8 * 1024 * 1024:
            raise ValueError('package module ambiguity')
        module = archive.extractfile(members[0]).read()
    if digest(module) != NEW:
        raise ValueError('reviewed package payload mismatch')
    timers = {name: command(['systemctl', 'is-active', name]).decode().strip() for name in spec['timers']}
    manifest = dict(version=2, spec=spec, machine=machine(), old=old, workers=workers,
                    preserved_configs=preserved, config_links=links,
                    listeners=listeners(old['identity'][0]), files=files, reader_files=readers,
                    unit=state, unit_text=digest(command(['systemctl', 'cat', spec['unit']])),
                    packages=packages(), timers=timers, initial=initial,
                    original=digest(original), target=digest(target), module=NEW,
                    source_package=PACKAGE, script=digest(read(__file__)),
                    package_action='unchanged; manual versioned module extracted from reviewed package')
    stage.mkdir(mode=0o700)
    # The only preparation writes are beneath this exclusive private stage.
    for name, data in [('load.original', original), ('load.candidate', target),
                       ('old.so', read(spec['old_module'])), ('candidate.so', module)]:
        atomic(stage / name, data, exclusive=True)
    raw = encoded(manifest)
    atomic(stage / 'manifest.json', raw, exclusive=True)
    sha = digest(raw)
    atomic(stage / 'journal.json', encoded(dict(phase='prepared', old=old['identity'], new=None, manifest_sha256=sha)), exclusive=True)
    return sha


class Linux:
    def __init__(self, stage, approved):
        if os.geteuid() != 0:
            raise PermissionError('root required')
        trusted(Path(__file__).absolute())
        self.stage = trusted(stage, private=True)
        raw = read(trusted(self.stage / 'manifest.json', private=True))
        if digest(raw) != approved:
            raise ValueError('immutable manifest approval mismatch')
        self.m = json.loads(raw)
        self.s = self.m['spec']
        if self.m['script'] != digest(read(__file__)) or self.m['machine'] != machine():
            raise ValueError('installer or machine/boot/netns changed')
        self.j = json.loads(read(trusted(self.stage / 'journal.json', private=True)))
        if self.j['manifest_sha256'] != approved or self.j['old'] != self.m['old']['identity']:
            raise ValueError('journal binding mismatch')
        self.clock = Deadlines(self.j, self.save)
        self.old = self.j['old']
        self.verify_backups()

    def save(self, journal):
        # Transaction phase snapshots must not overwrite newly reserved recovery.
        budget = dict(self.clock.budget) if hasattr(self, 'clock') else journal.get('budget')
        self.j = dict(journal)
        if budget is not None:
            self.j['budget'] = budget
        atomic(self.stage / 'journal.json', encoded(self.j))
        if hasattr(self, 'clock'):
            self.clock.journal = self.j

    def phase(self, name):
        self.clock.phase(name)

    def remaining(self):
        return self.clock.remaining()

    def wait(self, predicate):
        while True:
            self.remaining()
            try:
                value = predicate()
                if value:
                    return value
            except (FileNotFoundError, ProcessLookupError, ConnectionRefusedError):
                pass
            time.sleep(min(.1, self.remaining()))

    def verify_backups(self):
        for name, sha in [('load.original', self.m['original']), ('load.candidate', self.m['target']), ('old.so', OLD), ('candidate.so', NEW)]:
            if digest(read(trusted(self.stage / name, private=True))) != sha:
                raise ValueError('private backup corruption')
        for path, sha in self.s['retained_backups'].items():
            if digest(read(trusted(path))) != sha:
                raise ValueError('retained backup missing or corrupt')
        if digest(read(trusted(self.s['old_module']))) != OLD:
            raise ValueError('retained old mapped module changed')

    def verify_configs(self):
        links = {}
        current = config_inventory(self.s['config'], self.s.get('readonly_configs', {}), links)
        if set(current) != set(self.m['files']) or links != self.m['config_links']:
            raise ValueError('configuration include/link drift')
        for path, expected in self.m['preserved_configs'].items():
            if readonly_config(path, self.s.get('readonly_configs', {})) != expected:
                raise ValueError('preserved readonly configuration drift')
        # The sole writable config never inherits the readonly ownership exception.
        current_load = snapshot(trusted(self.s['load']))
        original_load = self.m['files'][self.s['load']]
        if (current_load['sha256'] not in (self.m['original'], self.m['target']) or
                any(current_load[k] != original_load[k] for k in ('uid', 'gid', 'mode', 'mtime_ns'))):
            raise ValueError('loadconf drift outside transaction')

    def preflight(self):
        self.verify_configs()
        self.verify_backups()
        if config_inventory(self.s['config']) != self.m['files'] or process(self.old[0]) != self.m['old'] or children(self.old) != self.m['workers'] or listeners(self.old[0]) != self.m['listeners']:
            raise ValueError('fresh host preflight drift')
        if unit_state(self.s['unit']) != self.m['unit'] or digest(command(['systemctl', 'cat', self.s['unit']])) != self.m['unit_text']:
            raise ValueError('unit drift')
        if digest(read(self.s['binary'])) != BINARY or packages() != self.m['packages']:
            raise ValueError('binary/package drift')
        for path, meta in self.m['reader_files'].items():
            if snapshot(trusted(path)) != meta:
                raise ValueError('reader drift')
        if Path(self.s['new_module']).exists() or Path(self.s['pid'] + '.oldbin').exists():
            raise ValueError('existing candidate or re-exec')

    def install(self):
        self.verify_configs()
        self.remaining()
        original_meta = self.m['files'][self.s['load']]
        if snapshot(trusted(self.s['load'])) != original_meta:
            raise ValueError('loadconf drift')
        atomic(Path(self.s['new_module']), read(self.stage / 'candidate.so'),
               dict(mode=0o644, uid=0, gid=0, mtime_ns=time.time_ns()), exclusive=True)
        self.remaining()
        atomic(Path(self.s['load']), read(self.stage / 'load.candidate'), original_meta)
        self.remaining()

    def syntax(self):
        self.verify_configs()
        command([self.s['binary'], '-p', self.s['prefix'], '-c', self.s['config'], '-t'], min(15, self.remaining()))

    def signal(self, who, name):
        self.verify_configs()
        self.remaining()
        try:
            p = process(who[0])
        except (FileNotFoundError, ProcessLookupError):
            if who != self.old and name == 'QUIT':
                return  # Resume after the new generation already exited.
            raise
        if p['cgroup'] != self.m['old']['cgroup'] or p['exe'] != self.s['binary']:
            raise ValueError('signal target namespace/cgroup mismatch')
        signal_identity(who, name)

    def discover_optional(self):
        pid = pidfile(self.s['pid'])
        if pid == self.old[0]:
            # Before/failed USR2: a child master or oldbin is ambiguous, not absent.
            if Path(self.s['pid'] + '.oldbin').exists():
                return self.discover()
            for token in read(f'/proc/{pid}/task/{pid}/children').split():
                if process(int(token))['title'].startswith('nginx: master process '):
                    raise ValueError('unpublished new master; preserve both for operator reconciliation')
            return None
        p = process(pid)
        if pidfile(self.s['pid'] + '.oldbin') != self.old[0] or p['parent'] != self.old[0] or p['identity'][1] < self.old[1] or p['cgroup'] != self.m['old']['cgroup'] or p['exe'] != self.s['binary'] or p['uid'] or not p['title'].startswith('nginx: master process '):
            raise ValueError('new master identity/lineage mismatch')
        if process(self.old[0])['identity'] != self.old:
            raise ValueError('old master reused')
        return p['identity']

    def discover(self):
        return self.wait(lambda: self.discover_optional() if pidfile(self.s['pid']) != self.old[0] else None)

    def ready(self, who):
        expected = self.s['old_module'] if who == self.old else self.s['new_module']
        other = self.s['new_module'] if who == self.old else self.s['old_module']
        def check():
            p = process(who[0])
            if p['identity'] != who or p['cgroup'] != self.m['old']['cgroup']:
                raise ValueError('master reused or moved')
            maps = read(f'/proc/{who[0]}/maps').decode()
            if (not mapping_matches(who[0], expected, OLD if who == self.old else NEW)
                    or not mapping_matches(who[0], self.s['binary'], BINARY)
                    or other in maps or expected + ' (deleted)' in maps):
                raise ValueError('fresh master module mappings invalid')
            workers = children(who)
            if len(workers) != self.s['workers']:
                return False
            if listeners(who[0]) != self.m['listeners']:
                raise ValueError('listener inheritance mismatch')
            for worker in workers:
                if process(worker[0])['title'] != 'nginx: worker process' or not set(self.m['listeners']) <= sockets(worker[0]):
                    return False
            return True
        return self.wait(check)

    def accept(self):
        def sample():
            value = reader_sample(self.s, self.m['initial']['state'])
            state = value['state']
            if not state or state['identity']['master'] != self.j['new']:
                return False
            if state['epoch'] == self.m['initial']['state']['epoch'] or any(row['sync_tx_bps'] is not None for row in value['rows'].values()):
                raise ValueError('reader generation transition did not reset')
            return value
        first = self.wait(sample)
        stable = reader_sample(self.s, first['state'])
        if not stable['state'] or stable['state']['epoch'] != first['state']['epoch'] or stable['state']['identity'] != first['state']['identity']:
            raise ValueError('candidate reader epoch not stable')
        # Production uncertainty may be caused by unrelated traffic. Record it;
        # exact zero/byte semantics are proved separately in the isolated fixture.
        results = [probe(p, min(10, self.remaining())) for p in self.s['probes']]
        results.append(probe(dict(url=self.s['error_url'] + uuid.uuid4().hex, code=404), min(10, self.remaining())))
        atomic(self.stage / 'acceptance.json', encoded(dict(first=first, stable=stable, probes=results)))

    def drain(self, who):
        def check():
            try:
                p = process(who[0])
            except (ProcessLookupError, FileNotFoundError):
                return True
            if p['identity'] != who:
                raise ValueError('PID reused during drain; never signal it')
            return not children(who)
        self.wait(check)

    def adopted(self, who):
        def check():
            s = unit_state(self.s['unit'])
            return (s['ActiveState'] == 'active' and s['SubState'] == 'running' and s['Result'] == 'success'
                    and int(s['MainPID']) == who[0] and pidfile(self.s['pid']) == who[0]
                    and process(who[0])['identity'] == who)
        self.wait(check)
        self.ready(who)
        if self.j.get('new') and who == self.old:
            self.wait(lambda: not Path(f'/proc/{self.j["new"][0]}').exists())
        elif who != self.old:
            self.wait(lambda: not Path(f'/proc/{self.old[0]}').exists())

    def restore(self):
        self.verify_configs()
        self.verify_backups()
        original = self.m['files'][self.s['load']]
        current = snapshot(trusted(self.s['load']))
        if current['sha256'] not in (self.m['target'], self.m['original']) or any(current[k] != original[k] for k in ('uid', 'gid', 'mode', 'mtime_ns')):
            raise ValueError('rollback refuses unrelated loadconf drift')
        atomic(Path(self.s['load']), read(self.stage / 'load.original'), original)
        if snapshot(self.s['load']) != original:
            raise ValueError('exact file rollback failed')
        # Never remove candidate: a draining process can still map it.

    def finish(self):
        self.verify_configs()
        if packages() != self.m['packages']:
            raise ValueError('package inventory changed')
        for name, expected in self.m['timers'].items():
            if command(['systemctl', 'is-active', name]).decode().strip() != expected:
                raise ValueError('timer state changed')
        results = [probe(p, min(10, self.remaining())) for p in self.s['probes']]
        atomic(self.stage / 'receipt.json', encoded(dict(package_inventory_unchanged=True,
               package_action=self.m['package_action'], probes=results,
               manual_module_sha256=NEW, source_package_sha256=PACKAGE)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prep', 'apply', 'rollback', 'reconcile'))
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--approve-manifest-sha256')
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error('root required')
    os.umask(0o077)
    if args.action == 'prep':
        if not args.inputs:
            parser.error('--inputs required')
        print(prepare(json.loads(read(trusted(args.inputs, private=True))), args.stage))
        return
    if not args.approve_manifest_sha256:
        parser.error('explicit reviewed manifest SHA256 required')
    # Cross-stage lock prevents simultaneous transactions of this nginx instance.
    fd = os.open('/run/lock/keimirror-http-reexec.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        if s.st_uid or not stat.S_ISREG(s.st_mode) or s.st_mode & 0o077:
            raise ValueError('unsafe lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        b = Linux(args.stage, args.approve_manifest_sha256)
        tx = Transaction(b, b.j)
        if args.action == 'apply':
            tx.apply()
        elif args.action == 'rollback':
            tx.rollback()
        else:
            if tx.j['phase'] != 'commit-intent' or not tx.j['new']:
                raise ValueError('only commit-intent can reconcile; otherwise rollback')
            b.phase('adoption'); b.adopted(tx.j['new']); b.finish(); tx.mark('committed')
        print(json.dumps(tx.j))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        os.close(fd)


if __name__ == '__main__':
    main()
