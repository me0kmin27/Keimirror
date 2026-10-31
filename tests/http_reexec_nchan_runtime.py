"""Root-only, disposable full-module collision regression. Never production.

Artifacts are copied from the read-only host inventory, SHA pinned before exec.
This deliberately exercises rollback primitives below the production Nchan gate;
it is NOT an approval to bypass that gate or proof of systemd/message continuity.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import time


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def cleanup_fixture(tx, root, owned):
    """Best effort per target; failures are aggregated, never hidden as success."""
    errors = []
    targets = {tuple(who) for who in owned}
    for suffix in ('', '.oldbin'):
        path = root / ('run/nginx.pid' + suffix)
        try:
            # Missing names are normal after exit/rename; retained identities below
            # still get checked. Only typed publication churn gets a retry.
            tx.trusted(path)
            acquire_end = time.monotonic() + 1
            while True:
                try:
                    pid = tx.published_pid(path)
                    break
                except tx.PIDPublicationPending:
                    if time.monotonic() >= acquire_end:
                        raise TimeoutError('cleanup publication deadline')
                    time.sleep(.05)
            who = tuple(tx.process(pid)['identity'])
            if any(previous[0] == pid and previous != who for previous in targets):
                raise ValueError('cleanup PID reused; retained start cannot be replaced')
            targets.add(who)
        except (FileNotFoundError, ProcessLookupError):
            pass
        except Exception as exc:
            errors.append(f'{path}: {exc!r}')
    pending = []
    for target in sorted(targets):
        who = list(target)
        try:
            proc = tx.process(who[0])
            try:
                process_root = os.readlink(f'/proc/{who[0]}/root')
            except OSError as exc:
                raise ValueError(f'cleanup chroot unverified: {exc!r}') from exc
            if (proc['identity'] != who or proc['exe'] != str(root / 'usr/sbin/nginx') or
                    process_root != str(root) or proc['uid'] or
                    not proc['title'].startswith('nginx: master process ')):
                raise ValueError('cleanup refuses unproved identity/executable/chroot')
            tx.signal_identity(who, 'QUIT')
            pending.append(who)
        except (FileNotFoundError, ProcessLookupError):
            pass
        except Exception as exc:
            errors.append(f'{who}: {exc!r}')
    end = time.monotonic() + 5
    while pending:
        for who in pending[:]:
            try:
                if tx.process(who[0])['identity'] != who:
                    raise ValueError('cleanup PID reused; absence unverified')
            except (FileNotFoundError, ProcessLookupError):
                pending.remove(who)
            except Exception as exc:
                errors.append(f'{who}: {exc!r}')
                pending.remove(who)
        if not pending:
            break
        if time.monotonic() >= end:
            errors.append(f'cleanup absence deadline: {pending!r}')
            break
        time.sleep(.05)
    if errors:
        raise RuntimeError('cleanup incomplete: ' + '; '.join(errors))


def run(args):
    assert os.geteuid() == 0
    for p in (args.source, args.archive, args.manifest, args.candidate, args.root.parent):
        assert p.is_absolute() and p.resolve(strict=True) == p
        for part in [*p.parents, p]:
            s = part.lstat()
            assert s.st_uid == 0 and s.st_gid == 0 and not s.st_mode & 0o022
    assert sha(args.source.read_bytes()) == args.source_sha
    manifest = json.loads(args.manifest.read_bytes())
    assert sha(args.manifest.read_bytes()) == args.manifest_sha
    blob = args.archive.read_bytes()
    assert sha(blob) == manifest['sha256']
    candidate = args.candidate.read_bytes()
    assert sha(candidate) == 'a5035e055ece858073606d8e4952167286914eafb8704e2cfa29a2bc97e247e5'
    spec = importlib.util.spec_from_file_location('tx', args.source)
    tx = importlib.util.module_from_spec(spec); spec.loader.exec_module(tx)
    root = args.root
    assert root.parent.stat().st_uid == 0 and not root.exists()
    root.mkdir(mode=0o700)
    # Cover startup failures too, before the main rollback test's finally block.
    import atexit
    owned = []
    def cleanup_startup():
        cleanup_fixture(tx, root, owned)
    atexit.register(cleanup_startup)
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        for item in archive:
            assert item.isfile() and not item.name.startswith('/') and '..' not in Path(item.name).parts
            data = archive.extractfile(item).read()
            assert sha(data) == manifest['files']['/' + item.name]['sha256']
            dest = root / item.name; dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data); dest.chmod(item.mode)
    for base in ('usr/share/perl', 'usr/lib/x86_64-linux-gnu/perl'):
        directory = root / base
        if (directory / '5.40.1').exists():
            (directory / '5.40').symlink_to('5.40.1')
    for path in ('etc/nginx', 'run', 'logs', 'var/log/nginx', 'var/lib/nginx/body', 'www'):
        (root / path).mkdir(parents=True, exist_ok=True)
    import stat
    (root / 'dev').mkdir()
    os.mknod(root / 'dev/null', stat.S_IFCHR | 0o666, os.makedev(1, 3))
    # chroot uses only pinned loader/dependencies; no host /etc/nginx is read.
    (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/false\n')
    (root / 'etc/group').write_text('root:x:0:\n')
    newpath = '/usr/lib/nginx/modules/project-candidate.so'
    (root / newpath.lstrip('/')).write_bytes(candidate)
    oldpath = '/usr/lib/nginx/modules/ngx_http_project_live_module.so'
    assert sha((root / oldpath.lstrip('/')).read_bytes()) == tx.OLD
    binary = root / 'usr/sbin/nginx'
    assert sha(binary.read_bytes()) == tx.BINARY
    modules = manifest['modules']
    # Dependency modules precede consumers, matching the enabled production set.
    modules = sorted(modules, key=lambda p: (0 if p.endswith(('ndk_http_module.so', 'ngx_stream_module.so')) else 1, p))
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    load = root / 'etc/nginx/project.conf'
    original = f'load_module {oldpath};\n'.encode()
    load.write_bytes(original)
    config = root / 'etc/nginx/canary.conf'
    config.write_text(''.join(f'load_module {p};\n' for p in modules if p != oldpath) + f'''
include /etc/nginx/project.conf;
user root;
pid /run/nginx.pid;
error_log /logs/error.log notice;
worker_processes {args.workers};
events {{ worker_connections 128; }}
http {{ access_log off; client_body_temp_path /var/lib/nginx/body;
server {{ listen 127.0.0.1:{port}; root /www;
location /slow {{ limit_rate 1k; }}
}} }}
''')
    body = b'protected-full-module-body\n'
    (root / 'www/body').write_bytes(body)
    (root / 'www/slow').write_bytes(b'x' * 1024 * 1024)
    protected = {p: sha(p.read_bytes()) for p in (config, root / 'www/body', root / 'www/slow')}
    argv = ['chroot', str(root), '/usr/sbin/nginx', '-p', '/', '-c', '/etc/nginx/canary.conf']
    def command(extra=()):
        result = subprocess.run(argv + list(extra), capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError(result.stderr.decode())
        return result
    command(['-t'])
    command()
    deadline = time.monotonic() + 15
    def wait(fn):
        while time.monotonic() < deadline:
            try:
                result = fn()
                if result: return result
            except (FileNotFoundError, ProcessLookupError, ConnectionRefusedError):
                pass
            except tx.ProcessSnapshotChurn:
                pass
            time.sleep(.05)
        raise TimeoutError('isolated runtime deadline')
    old = tx.process(wait(lambda: tx.pidfile(root / 'run/nginx.pid')))['identity']
    owned.append(old)
    new = None; held = None
    signals = []
    b = tx.Linux.__new__(tx.Linux)
    b.old = old
    b.s = dict(pid=str(root / 'run/nginx.pid'), binary=str(binary), workers=args.workers,
               old_module=str(root / oldpath.lstrip('/')), new_module=str(root / newpath.lstrip('/')))
    b.m = dict(old=tx.process(old[0]), listeners=tx.listeners(old[0]))
    b.remaining = lambda: max_remaining(deadline)
    b.verify_configs = lambda: verify(protected)
    b.m['workers'] = wait(lambda: (w if len(w := tx.children(old)) == args.workers else None))
    b.ready(old)
    original_workers = b.m['workers']
    def probe():
        with socket.create_connection(('127.0.0.1', port), timeout=2) as s:
            s.sendall(b'GET /body HTTP/1.0\r\n\r\n')
            data = b''
            while chunk := s.recv(4096): data += chunk
        assert data.split(b'\r\n\r\n', 1)[1] == body
        return sha(body)
    def save(j):
        b.j = dict(j); tx.atomic(root / 'journal.json', tx.encoded(j))
    def signal(who, name):
        signals.append([who, name]); tx.Linux.signal(b, who, name)
    b.save = save; b.signal = signal
    b.phase = lambda name: max_remaining(deadline)  # one unchanged test deadline
    b.restore = lambda: load.write_bytes(original)
    b.syntax = lambda: command(['-t'])
    b.finish = lambda: (verify(protected), probe())
    def adopted(who):
        wait(lambda: tx.pidfile(root / 'run/nginx.pid') == who[0] and not Path(f'/proc/{new[0]}').exists())
        b.ready(who)
    b.adopted = adopted
    outcome = {}
    try:
        probe()
        if args.mode == 'partial':
            held = socket.create_connection(('127.0.0.1', port), timeout=2)
            held.sendall(b'GET /slow HTTP/1.0\r\n\r\n'); assert held.recv(1024)
        load.write_text(f'load_module {newpath};\n')
        signal(old, 'USR2'); new = b.discover()
        owned.append(new)
        new_workers = wait(lambda: (w if len(w := tx.children(new)) == args.workers else None))
        b.ready(new)
        # Every production dynamic module, including Nchan, is actually mapped.
        maps = tx.read(f'/proc/{new[0]}/maps').decode()
        assert all(str(root / p.lstrip('/')) in maps for p in modules if p != oldpath)
        if args.mode in ('drained', 'partial'):
            signal(old, 'WINCH')
            if args.mode == 'drained': b.drain(old)
            else: wait(lambda: 0 < len(tx.children(old)) < args.workers)
        save(dict(phase='winch-intent' if args.mode in ('drained', 'partial') else 'new-discovered', old=old, new=new))
        deadline = time.monotonic() + (3 if args.mode in ('red', 'partial') else 12)
        error = None
        try: tx.Transaction(b, b.j).rollback()
        except Exception as exc: error = type(exc).__name__
        log = (root / 'logs/error.log').read_text()
        collision = 'procslot_found == 1' in log
        # libc assertions are also captured from the inherited daemon stderr below.
        if args.mode == 'red':
            assert error and [old, 'HUP'] in signals
            assert 'exited on signal 6' in log or collision, log[-2000:]
            assert collision, 'exact Nchan procslot assertion missing'
            assert all(tx.process(w[0])['identity'] == w for w in original_workers)
            assert tx.children(new) == new_workers
        elif args.mode == 'partial':
            assert error and [old, 'HUP'] not in signals and [new, 'QUIT'] not in signals
            assert tx.children(new) == new_workers
            assert b.j['phase'] != 'rolled-back'
            probe()
        else:
            assert error is None, (error, log[-2000:])
            assert b.j['phase'] == 'rolled-back'
            if args.mode == 'green':
                assert [old, 'HUP'] not in signals and tx.children(old) == original_workers
            stable = tx.children(old)
            for _ in range(10):
                time.sleep(.2); assert tx.children(old) == stable; probe(); verify(protected)
            assert 'exited on signal 6' not in log and not collision
        outcome = dict(mode=args.mode, workers=args.workers, loaded_modules=len(modules),
                       source_sha256=args.source_sha, nchan_sha256=manifest['files']['/usr/lib/nginx/modules/ngx_nchan_module.so']['sha256'],
                       signal_order=[name for _, name in signals], rollback_error=error,
                       phase=b.j['phase'], abort_observed='exited on signal 6' in log,
                       original_workers_unchanged=(all(tx.process(w[0])['identity'] == w for w in original_workers) if args.mode == 'red' else tx.children(old)==original_workers),
                       exact_nchan_assertion=collision,
                       body_sha256=probe(), protected_unchanged=True)
    finally:
        if held: held.close()
        try:
            cleanup_startup()
        finally:
            atexit.unregister(cleanup_startup)
    print(json.dumps(outcome), flush=True)
    (root / 'result.json').write_text(json.dumps(outcome, indent=2))


def verify(paths):
    assert all(sha(p.read_bytes()) == value for p, value in paths.items())


def max_remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0: raise TimeoutError('same isolated phase budget exhausted')
    return left


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('source', 'archive', 'manifest', 'candidate', 'root'):
        p.add_argument('--' + name, type=Path, required=True)
    for name in ('source-sha', 'manifest-sha'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--mode', choices=('red', 'green', 'drained', 'partial'), required=True)
    p.add_argument('--workers', type=int, choices=(2, 32), required=True)
    run(p.parse_args())
