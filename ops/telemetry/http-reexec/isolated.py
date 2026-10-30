"""Opt-in root integration. Only task-owned systemd units/files, no production signals.

Arguments: evidence directory containing nginx, old.so, candidate.deb and reader/
(http_rates.py, private_reasons.py). Run through the reviewed root_stage entry.
Artifacts are captured and verified before transaction import or any command.
"""
import argparse
import base64
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
import stat

# A direct root invocation from an operator checkout must reject before even
# importing its sibling verifier. Approved entry is root_stage's verified closure.
if os.geteuid() == 0:
    for source in (Path(__file__).absolute(), Path(__file__).absolute().with_name('root_stage.py')):
        for part in [*reversed(source.parents), source]:
            metadata = part.lstat()
            if metadata.st_uid or metadata.st_gid or metadata.st_mode & 0o022 or stat.S_ISLNK(metadata.st_mode):
                raise PermissionError('root-controlled source required before import')

import root_stage as provenance


def assert_candidate(proofs):
    """Isolated-only gate; production intentionally permits background uncertainty."""
    assert len(proofs) == 4 and {p['code'] for p in proofs} == {200, 403, 404, 416}
    for proof in proofs:
        request = base64.b64decode(proof['request_b64'], validate=True)
        response = base64.b64decode(proof['response_b64'], validate=True)
        assert len(request) <= 4096 and len(response) <= 262144
        header, body = response.split(b'\r\n\r\n', 1)
        assert response.startswith(f"HTTP/1.1 {proof['code']} ".encode())
        length = int(next(line.split(b':', 1)[1] for line in header.split(b'\r\n')
                          if line.lower().startswith(b'content-length:')))
        assert len(body) == length
        assert body == b'x' * 131072 if proof['code'] == 200 else f"<h1>{proof['code']} ".encode() in body
        samples = [proof[key] for key in ('before', 'first', 'second', 'final')]
        assert all(a['monotonic_ns'] < b['monotonic_ns'] for a, b in zip(samples, samples[1:]))
        rows = [s['status']['repositories']['archlinux'] for s in samples]
        assert all((r['uncertain'], r['active_uncertain']) == (0, 0) for r in rows)
        before, first, second, final = rows
        assert all(r['rx_bytes'] - before['rx_bytes'] == len(request) for r in rows[1:])
        assert len(header) + 4 < first['tx_bytes'] - before['tx_bytes'] < second['tx_bytes'] - before['tx_bytes'] < len(response)
        assert final['tx_bytes'] - before['tx_bytes'] == len(response)
        assert all(s['completion_bytes'] == samples[0]['completion_bytes'] for s in samples[1:3])
        assert samples[3]['completion_bytes'] > samples[0]['completion_bytes']
        for sample in samples[1:]:
            assert all(row == samples[0]['status']['repositories'][repo]
                       for repo, row in sample['status']['repositories'].items() if repo != 'archlinux')


def retain_acceptance(stage, output, evidence):
    """Persist exact production receipt plus raw isolated proof BEFORE cleanup."""
    provenance.trusted(output.parent)
    target = output.parent / (output.stem + '-acceptance')
    target.mkdir(mode=0o700)  # exclusive, no replacement of prior evidence
    raw = provenance.safe_read(stage / 'acceptance.json')
    files = {'acceptance.json': raw,
             'isolated-proof.json': json.dumps(evidence, indent=2).encode() + b'\n'}
    assert all(len(data) <= provenance.LIMIT for data in files.values())
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files['sha256.json'] = json.dumps(hashes, indent=2).encode() + b'\n'
    for name, data in files.items():
        with open(target / name, 'xb') as stream:
            os.chmod(target / name, 0o400)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        assert provenance.safe_read(target / name) == data
    return target


def wait_old_listeners_closed(t, backend):
    """WINCH is asynchronous; fence old WORKER accept FDs, not old master FDs."""
    def check():
        workers = t.children(backend.old)
        listeners = set(backend.m['listeners'])
        if any(listeners & t.sockets(worker[0]) for worker in workers):
            return False
        return dict(monotonic_ns=time.monotonic_ns(), wall_ns=time.time_ns(),
                    old=backend.old, workers=workers, accepting_listeners=[])
    return backend.wait(check)


def status_sample(t, status, master, log) -> dict:
    before = t.children(master)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(status))
        client.sendall(b'GET / HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        raw = b''
        while part := client.recv(4096):
            raw += part
            assert len(raw) <= 262144
    assert raw.startswith(b'HTTP/1.1 200 ') or raw.startswith(b'HTTP/1.0 200 ')
    value = json.loads(raw.split(b'\r\n\r\n', 1)[1])
    assert before == t.children(master)
    assert t.process(master[0])['identity'] == master
    assert set(value['workers']) == {worker[0] for worker in before}
    completion = provenance.safe_read(log, limit=65536)
    return dict(monotonic_ns=time.monotonic_ns(), wall_ns=time.time_ns(),
                master=master, workers=before, status=value,
                raw_b64=base64.b64encode(raw).decode(),
                completion_b64=base64.b64encode(completion).decode(),
                completion_bytes=len(completion))


def collect_case(t, port, status, master, root, code, path, headers=b'', progress=True):
    """Reuse nginx-live/tests/test_errors.py wire cases and completion-log oracle."""
    log = root / ('access-' + str(code))
    sample = lambda: status_sample(t, status, master, log)
    proof: dict = dict(code=code, before=sample())
    request = (b'GET ' + path.encode() + b' HTTP/1.1\r\nHost: localhost\r\n'
               b'Connection: close\r\n' + headers + b'\r\n')
    proof['request_b64'] = base64.b64encode(request).decode()
    with socket.create_connection(('127.0.0.1', port), timeout=10) as client:
        client.sendall(request)
        response = b''
        # Require actual BODY bytes before the first live sample, not just headers.
        while b'\r\n\r\n' not in response or not response.split(b'\r\n\r\n', 1)[1]:
            part = client.recv(64)
            assert part
            response += part
            assert len(response) <= 262144
        proof['first'] = sample()
        if progress:
            end = time.monotonic() + 3
            while True:
                second = sample()
                if second['status']['repositories']['archlinux']['tx_bytes'] > proof['first']['status']['repositories']['archlinux']['tx_bytes']:
                    break
                assert time.monotonic() < end
                time.sleep(.05)
            proof['second'] = second
        while part := client.recv(4096):
            response += part
            assert len(response) <= 262144
    end = time.monotonic() + 2
    while True:
        proof['final'] = sample()
        if proof['final']['completion_bytes'] > proof['before']['completion_bytes']:
            break
        assert time.monotonic() < end
        time.sleep(.05)
    assert response.startswith(f'HTTP/1.1 {code} '.encode())
    proof['response_b64'] = base64.b64encode(response).decode()
    return proof


def run(artifacts, output, case):
    assert os.geteuid() == 0
    provenance.trusted(Path(__file__).absolute())
    provenance.trusted(Path(provenance.__file__).absolute())
    root = Path('/var/lib/keimirror-reexec-test-' + uuid.uuid4().hex[:10])
    provenance.trusted(root.parent)
    root.mkdir(mode=0o755)
    provenance.trusted(root)
    unit = root.name + '.service'
    runtime = Path('/run') / root.name
    client = None
    result = {'case': case, 'passed': False}
    launched = False
    t = None
    stage = None
    evidence: dict = {}
    try:
        private = provenance.stage_artifacts(artifacts, root / 'private')
        # Load captured source, never an existing sys.modules entry or pyc.
        import types
        path = provenance.trusted(Path(__file__).absolute().with_name('transaction.py'))
        t = types.ModuleType('isolated_transaction'); t.__file__ = str(path)
        exec(compile(provenance.safe_read(path), str(path), 'exec'), t.__dict__)
        (root / 'archlinux').mkdir()
        for name in ('file', 'success', 'slow'):
            (root / 'archlinux' / name).write_bytes(b'x' * 131072)
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
http {{ access_log off; log_format proof '$status'; project_live_zone;
client_body_temp_path {root}/body; proxy_temp_path {root}/proxy;
fastcgi_temp_path {root}/fastcgi; uwsgi_temp_path {root}/uwsgi; scgi_temp_path {root}/scgi;
server {{ listen 127.0.0.1:{port}; root {root}; project_live on; limit_rate 32768;
location = /archlinux/denied {{ deny all; limit_rate 64; access_log {root}/access-403 proof; }}
location = /archlinux/missing {{ limit_rate 64; access_log {root}/access-404 proof; }}
location = /archlinux/file {{ limit_rate 64; access_log {root}/access-416 proof; }}
location = /archlinux/success {{ access_log {root}/access-200 proof; }} }}
server {{ listen unix:{runtime}/status.sock; project_live off; location = / {{ project_live_status; }} }}
}}
''')
        command = ['systemd-run', '--unit=' + unit, '-p', 'Type=forking', '-p', 'PIDFile=' + str(runtime / 'pid'), '-p', 'RuntimeDirectory=' + root.name, '-p', 'RuntimeDirectoryMode=0700', '-p', 'GuessMainPID=yes', '-p', 'NotifyAccess=none', '-p', 'KillMode=mixed', '-p', 'Restart=no', '-p', 'TimeoutStopSec=5', '-p', 'ExecStop=-/sbin/start-stop-daemon --quiet --stop --retry QUIT/5 --pidfile ' + str(runtime / 'pid'), str(private / 'nginx'), '-p', str(root) + '/', '-c', str(conf)]
        launched = True
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
                    probes=[dict(url=f'http://127.0.0.1:{port}/archlinux/slow', code=200, size=131072, sha256=t.digest(b'x' * 131072))],
                    error_url=f'http://127.0.0.1:{port}/archlinux/reexec-404-')
        stage = private / 'transaction'
        manifest_sha = t.prepare(spec, stage)
        backend = t.Linux(stage, manifest_sha)
        journal = json.loads(t.read(stage / 'journal.json'))
        old = journal['old']
        negative = collect_case(t, port, runtime / 'status.sock', old, root,
                                403, '/archlinux/denied', progress=False)
        assert negative['final']['status']['repositories']['archlinux']['uncertain'] >= 1
        evidence = dict(old_negative=negative, candidate=[])
        original_accept = backend.accept
        def isolated_accept():
            evidence['listener_fence'] = wait_old_listeners_closed(t, backend)
            # Capture the normal, deliberately permissive production acceptance.
            original_accept()
            proofs = evidence['candidate']
            for code, path, headers in (
                (200, '/archlinux/success', b''),
                (403, '/archlinux/denied', b''),
                (404, '/archlinux/missing', b''),
                (416, '/archlinux/file', b'Range: bytes=999999-\r\n'),
            ):
                proofs.append(collect_case(t, port, runtime / 'status.sock',
                                           backend.j['new'], root, code, path, headers))
            assert_candidate(proofs)
            evidence['accepted_monotonic_ns'] = time.monotonic_ns()
            evidence['accepted_wall_ns'] = time.time_ns()
        backend.accept = isolated_accept
        client = socket.create_connection(('127.0.0.1', port), timeout=15)
        client.sendall(b'GET /archlinux/slow HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        first = client.recv(4096)
        assert b'200 OK' in first
        evidence['slow_first_b64'] = base64.b64encode(first).decode()
        evidence['slow_first_monotonic_ns'] = time.monotonic_ns()
        evidence['slow_first_wall_ns'] = time.time_ns()
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
        while chunk := client.recv(65536):
            body += chunk
            assert len(body) <= 262144
        header, payload = body.split(b'\r\n\r\n', 1)
        assert payload == b'x' * 131072
        assert 'accepted_monotonic_ns' in evidence
        assert_candidate(evidence['candidate'])
        evidence['slow_response_b64'] = base64.b64encode(body).decode()
        evidence['slow_completed_monotonic_ns'] = time.monotonic_ns()
        evidence['slow_completed_wall_ns'] = time.time_ns()
        result.update(passed=True, body_bytes=len(payload), response_bytes=len(body), phase=tx.j['phase'], manifest_sha256=manifest_sha, journal=tx.j)
    finally:
        if t is not None:
            t.signal.setitimer(t.signal.ITIMER_REAL, 0)
            t.ACTIVE_DEADLINE = None
        if client: client.close()
        if launched:
            result['unit'] = subprocess.run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState', '-p', 'Result'], capture_output=True, text=True, timeout=15).stdout
        if (root / 'error.log').exists(): result['nginx_log'] = (root / 'error.log').read_text()
        try:
            if stage is not None and (stage / 'acceptance.json').exists():
                evidence['retained_monotonic_ns'] = time.monotonic_ns()
                evidence['retained_wall_ns'] = time.time_ns()
                result['acceptance_directory'] = str(retain_acceptance(stage, output, evidence))
        finally:
            if launched:
                subprocess.run(['systemctl', 'stop', unit], capture_output=True, timeout=15)
        # Retention failure leaves the source receipts in place, but still stops the unit.
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
