"""Opt-in root integration. Only task-owned systemd units/files, no production signals.

Arguments: evidence directory containing nginx, old.so, candidate.deb and reader/
(http_rates.py, private_reasons.py). Artifacts are checked by transaction.prepare.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
import transaction as t


def run(artifacts, output, case):
    assert os.geteuid() == 0
    t.trusted(Path(__file__).absolute())
    t.trusted(Path(t.__file__).absolute())
    assert hasattr(t, 'prepare'), 'root preparation missing'
    root = Path('/var/lib/keimirror-reexec-test-' + uuid.uuid4().hex[:10])
    root.mkdir(mode=0o755)
    unit = root.name + '.service'
    runtime = Path('/run') / root.name
    client = None
    result = {'case': case, 'passed': False}
    try:
        private = root / 'private'; private.mkdir(mode=0o700)
        for name in ('nginx', 'old.so', 'candidate.deb'):
            shutil.copyfile(artifacts / name, private / name)
            (private / name).chmod(0o755 if name == 'nginx' else 0o600)
        shutil.copytree(artifacts / 'reader', private / 'reader')
        (root / 'archlinux').mkdir(); (root / 'archlinux/file').write_bytes(b'x' * 131072)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        load = private / 'load.conf'
        load.write_text(f'load_module {private}/old.so;\n'); load.chmod(0o664)
        conf = root / 'nginx.conf'
        conf.write_text(f'''include {load};
user www-data;
worker_processes 1;
pid {runtime}/pid;
error_log {root}/error.log notice;
events {{ worker_connections 64; }}
http {{ access_log off; project_live_zone;
client_body_temp_path {root}/body; proxy_temp_path {root}/proxy;
fastcgi_temp_path {root}/fastcgi; uwsgi_temp_path {root}/uwsgi; scgi_temp_path {root}/scgi;
server {{ listen 127.0.0.1:{port}; root {root}; project_live on; limit_rate 32768;
location = /archlinux/absent {{ limit_rate 64; }} }}
server {{ listen unix:{runtime}/status.sock; project_live off; location = / {{ project_live_status; }} }}
}}
''')
        command = ['systemd-run', '--unit=' + unit, '-p', 'Type=forking', '-p', 'PIDFile=' + str(runtime / 'pid'), '-p', 'RuntimeDirectory=' + root.name, '-p', 'RuntimeDirectoryMode=0700', '-p', 'GuessMainPID=yes', '-p', 'NotifyAccess=none', '-p', 'KillMode=mixed', '-p', 'Restart=no', '-p', 'TimeoutStopSec=5', '-p', 'ExecStop=-/sbin/start-stop-daemon --quiet --stop --retry QUIT/5 --pidfile ' + str(runtime / 'pid'), str(private / 'nginx'), '-p', str(root) + '/', '-c', str(conf)]
        subprocess.run(command, check=True, capture_output=True, timeout=15)
        end = time.monotonic() + 5
        while not (runtime / 'status.sock').exists():
            assert time.monotonic() < end; time.sleep(.05)
        spec = dict(hostname=os.uname().nodename, binary=str(private / 'nginx'),
                    config=str(conf), prefix=str(root) + '/', pid=str(runtime / 'pid'),
                    load=str(load), old_module=str(private / 'old.so'),
                    new_module=str(private / ('ngx_http_project_live_module-' + t.NEW + '.so')),
                    package=str(private / 'candidate.deb'), unit=unit, workers=1,
                    status=str(runtime / 'status.sock'), reader_dir=str(private / 'reader'),
                    timers=[], retained_backups={str(private / 'old.so'): t.OLD},
                    load_sha256=t.digest(load.read_bytes()),
                    probes=[dict(url=f'http://127.0.0.1:{port}/archlinux/file', code=200, size=131072, sha256=t.digest(b'x' * 131072))],
                    error_url=f'http://127.0.0.1:{port}/archlinux/reexec-404-')
        stage = private / 'transaction'
        manifest_sha = t.prepare(spec, stage)
        backend = t.Linux(stage, manifest_sha)
        journal = json.loads(t.read(stage / 'journal.json'))
        old = journal['old']
        client = socket.create_connection(('127.0.0.1', port), timeout=15)
        client.sendall(b'GET /archlinux/file HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        first = client.recv(4096)
        assert b'200 OK' in first
        original_phase = backend.phase
        if case == 'rollback':
            def fail(name):
                original_phase(name)
                if name == 'drain':
                    raise TimeoutError('injected drain deadline after WINCH')
            backend.phase = fail
        tx = t.Transaction(backend, journal)
        if case == 'rollback':
            try:
                tx.apply()
            except TimeoutError:
                pass
            assert tx.j['phase'] == 'rolled-back', tx.j
            assert t.process(old[0])['identity'] == old
        else:
            tx.apply(); assert tx.j['phase'] == 'committed'
        body = first
        while chunk := client.recv(65536): body += chunk
        header, payload = body.split(b'\r\n\r\n', 1)
        assert payload == b'x' * 131072
        result.update(passed=True, body_bytes=len(payload), response_bytes=len(body), phase=tx.j['phase'], manifest_sha256=manifest_sha, journal=tx.j)
    finally:
        t.signal.setitimer(t.signal.ITIMER_REAL, 0)
        t.ACTIVE_DEADLINE = None
        if client: client.close()
        result['unit'] = subprocess.run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState', '-p', 'Result'], capture_output=True, text=True).stdout
        if (root / 'error.log').exists(): result['nginx_log'] = (root / 'error.log').read_text()
        subprocess.run(['systemctl', 'stop', unit], capture_output=True, timeout=15)
        shutil.rmtree(root)
        result['cleanup'] = not root.exists() and not runtime.exists()
        output.write_text(json.dumps(result, indent=2))
    assert result['passed'] and result['cleanup']
    print(json.dumps({k: result[k] for k in ('case', 'passed', 'body_bytes', 'phase', 'cleanup')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('artifacts', type=Path); parser.add_argument('output', type=Path)
    parser.add_argument('--case', choices=('forward', 'rollback'), default='forward')
    args = parser.parse_args(); run(args.artifacts, args.output, args.case)
