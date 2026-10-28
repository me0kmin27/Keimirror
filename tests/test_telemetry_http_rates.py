import copy
import importlib
import json
import unittest
from unittest import mock
import os
from pathlib import Path
import socket
import tempfile
import threading
import time


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
                              if name.startswith('test_') and callable(value))

CONFIG = {'repositories': {f'repo{i}': {} for i in range(14)}}
IDENTITY = {'master': [10, 100], 'workers': [[11, 101]], 'boot': 1}


def module():
    return importlib.import_module('telemetry.http_rates')


def payload():
    return {'schema_version': 1, 'epoch': 'generation1', 'workers': [11],
            'repositories': {rid: dict(rx_bytes=100, tx_bytes=200, uncertain=0, active_uncertain=0)
                             for rid in CONFIG['repositories']}}


def tick(previous=None, data=None, mono=1, **kwargs):
    return module().sample(CONFIG, previous, now=1000 + mono, monotonic_now=mono,
                           reader=lambda: json.dumps(payload() if data is None else data).encode(),
                           workers_reader=lambda root: copy.deepcopy(IDENTITY), **kwargs)


def test_first_unknown_then_monotonic_delta_and_verified_zero():
    rows, state = tick()
    assert all(row['sync_rx_bps'] is None for row in rows.values())
    data = payload()
    data['repositories']['repo0']['rx_bytes'] += 20
    rows, state = tick(state, data, 1.5)
    assert rows['repo0'] == dict(sync_rx_bps=40, sync_tx_bps=0, rates_observed_at=1001.5)
    rows, state = tick(state, data, 2)
    assert rows['repo0']['sync_rx_bps'] == 0
    assert set(rows) == set(CONFIG['repositories'])


def test_invalid_schema_and_generation_never_emit_rates_or_raw_state():
    _, previous = tick()
    variants = []
    for value in (True, 1.5, -1, 2**64, '1', None):
        data = payload()
        data['repositories']['repo0']['rx_bytes'] = value
        variants.append(data)
    for key in ('schema_version', 'epoch', 'workers', 'repositories'):
        data = payload(); del data[key]; variants.append(data)
    data = payload(); data['secret'] = '/private/192.0.2.1'; variants.append(data)
    data = payload(); data['workers'] = [11, 11]; variants.append(data)
    data = payload(); data['workers'] = [12]; variants.append(data)
    data = payload(); data['repositories']['extra'] = {}; variants.append(data)
    data = payload(); del data['repositories']['repo0']; variants.append(data)
    data = payload(); data['repositories']['repo0']['secret'] = 'ip'; variants.append(data)
    for data in variants:
        rows, state = tick(previous, data, 2)
        assert all(v['sync_rx_bps'] is None for v in rows.values()), data
        assert state == {}, data
    for field, value in [('rx_bytes', 0), ('uncertain', 1), ('active_uncertain', 1)]:
        data = payload(); data['repositories']['repo0'][field] = value
        rows, state = tick(previous, data, 2)
        assert rows['repo0']['sync_rx_bps'] is None
    data = payload(); data['epoch'] = '/secret/ip'
    rows, state = tick(previous, data, 2)
    assert rows['repo0']['sync_rx_bps'] is None
    assert '/secret/ip' not in json.dumps(state)
    for mono in (1, .5, 3.01, float('nan'), float('inf'), True):
        rows, state = tick(previous, mono=mono)
        assert rows['repo0']['sync_rx_bps'] is None
    for malformed in ({'secret': '/private'}, {'monotonic': True}, []):
        rows, state = tick(malformed, mono=2)
        assert rows['repo0']['sync_rx_bps'] is None


def test_duplicate_json_and_worker_race_fail_closed():
    m = module()
    _, previous = tick()
    for raw in (b'{"schema_version":1,"schema_version":1}', b'[]', b'NaN', b'x' * 32769):
        rows, state = m.sample(CONFIG, previous, reader=lambda: raw,
                               workers_reader=lambda root: IDENTITY)
        assert state == {}
    snapshots = iter([IDENTITY, dict(IDENTITY, workers=[[11, 102]])])
    rows, state = m.sample(CONFIG, previous, now=1002, monotonic_now=2,
                           reader=lambda: json.dumps(payload()).encode(),
                           workers_reader=lambda root: next(snapshots))
    assert state == {}
    assert rows['repo0']['sync_rx_bps'] is None


def unix_response(response, delay=0):
    m = module()
    with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
        path = str(Path(directory) / 's')
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path); server.listen(1); server.settimeout(1)
        requests = []
        def serve():
            try:
                conn, _ = server.accept()
                with conn:
                    requests.append(conn.recv(4096))
                    time.sleep(delay)
                    conn.sendall(response)
            except (BrokenPipeError, ConnectionResetError):
                pass
        thread = threading.Thread(target=serve)
        thread.start()
        try:
            with mock.patch.object(m, 'SOCKET_PATH', path):
                result = m.sample(CONFIG, None, workers_reader=lambda root: IDENTITY)
        finally:
            thread.join(2); server.close()
        assert requests == [b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n']
        return result


def test_real_unix_http_strict_framing_and_timeout():
    body = json.dumps(payload()).encode()
    good = b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
    assert unix_response(good)[1]
    bad = [good.replace(b'200 OK', b'302 Found'), good[:-1],
           good.replace(b'Content-Length:', b'Transfer-Encoding: chunked\r\nContent-Length:'),
           good.replace(b'Content-Length:', b'Content-Length: 1\r\nContent-Length:'),
           b'HTTP/1.1 200 OK\r\n\r\n' + body,
           b'HTTP/1.1 200 OK\r\nContent-Length: 32769\r\n\r\n',
           b'HTTP/1.1 200 OK\r\nX: ' + b'a' * 4096 + b'\r\n\r\n',
           good + b'extra']
    for response in bad:
        assert unix_response(response)[1] == {}
    start = time.monotonic()
    assert unix_response(good, delay=.4)[1] == {}
    assert time.monotonic()-start < 1


def test_realtime_is_after_read_and_total_deadline_covers_injected_readers():
    m = module()
    def slow():
        time.sleep(.31)
        return json.dumps(payload()).encode()
    assert m.sample(CONFIG, None, reader=slow, workers_reader=lambda root: IDENTITY)[1] == {}
    clock = [100]
    def read():
        clock[0] = 200
        return json.dumps(payload()).encode()
    _, previous = tick()
    with mock.patch.object(m.time, 'time', side_effect=lambda: clock[0]):
        rows, _ = m.sample(CONFIG, previous, reader=read, workers_reader=lambda root: IDENTITY,
                           monotonic_now=2)
    assert rows['repo0']['rates_observed_at'] == 200


def make_process(root, pid, parent, ticks, title):
    directory = root / str(pid)
    directory.mkdir(exist_ok=True)
    fields = ['S', str(parent)] + ['0'] * 17 + [str(ticks)] + ['0'] * 8
    (directory / 'stat').write_text(str(pid) + ' (nginx) ' + ' '.join(fields))
    (directory / 'cmdline').write_bytes(title.encode() + b'\0')
    exe = directory / 'exe'
    if not exe.exists():
        exe.symlink_to(root / 'nginx')


def test_proc_master_worker_generation_shutting_down_and_bounds():
    m = module()
    with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
        root = Path(directory)
        pidfile = root / 'nginx.pid'; pidfile.write_text('10\n'); pidfile.chmod(0o644)
        (root / 'nginx').write_bytes(b'fixture'); (root / 'nginx').chmod(0o755)
        boot = root / 'sys/kernel/random/boot_id'; boot.parent.mkdir(parents=True)
        boot.write_text('00000000-0000-4000-8000-000000000001')
        make_process(root, 10, 1, 100, 'nginx: master process /usr/sbin/nginx')
        make_process(root, 11, 10, 101, 'nginx: worker process')
        children = root / '10/task/10/children'; children.parent.mkdir(parents=True)
        children.write_text('11 ')
        def sample(previous=None, mono=1, data=None):
            return m.sample(CONFIG, previous, proc_root=root, now=1000+mono, monotonic_now=mono,
                            reader=lambda: json.dumps(data or payload()).encode())
        with mock.patch.object(m, 'PID_PATH', str(pidfile)), mock.patch.object(m, 'TRUSTED_UID', os.getuid()):
            rows, previous = sample()
            assert previous
            assert sample(previous, 2)[0]['repo0']['sync_rx_bps'] == 0
            make_process(root, 12, 10, 102, 'nginx: worker process is shutting down')
            children.write_text('11 12 ')
            assert sample(previous, 2)[1] == {}  # old unregistered worker: never zero
            data = payload(); data['workers'] = [11, 12]
            rows, fresh = sample(previous, 2, data)
            assert fresh and rows['repo0']['sync_rx_bps'] is None
            assert sample(fresh, 3, data)[0]['repo0']['sync_rx_bps'] == 0
            make_process(root, 11, 10, 999, 'nginx: worker process')
            assert sample(fresh, 3, data)[0]['repo0']['sync_rx_bps'] is None
            pidfile.chmod(0o666)
            assert sample()[1] == {}
            pidfile.chmod(0o644)
            children.write_text('11 ' * 1025)
            assert sample()[1] == {}
            children.write_text('11 ')
            original = m.bounded
            calls = [0]
            def racing(path, limit, deadline, trusted=False):
                raw = original(path, limit, deadline, trusted)
                if Path(path) == children:
                    calls[0] += 1
                    if calls[0] == 2:
                        return b'11 12 '
                return raw
            with mock.patch.object(m, 'bounded', side_effect=racing):
                assert sample()[1] == {}
            master_stat = root / '10/stat'
            saved_stat = master_stat.read_text()
            master_stat.write_text(saved_stat.replace(' 100 ', ' 200 '))
            rows, _ = sample(previous, 2)
            assert rows['repo0']['sync_rx_bps'] is None
            master_stat.write_text(saved_stat)
            (root / '10/cmdline').write_bytes(b'not nginx\0')
            assert sample()[1] == {}
            (root / '10/cmdline').write_bytes(b'nginx: master process /usr/sbin/nginx\0')
            pidfile.unlink(); pidfile.symlink_to(root / 'nginx')
            assert sample()[1] == {}
            pidfile.unlink(); pidfile.write_text('10\n')
            (root / '11/cmdline').write_bytes(b'x' * 8193)
            assert sample()[1] == {}


def test_uint64_precision_recovery_and_private_state_allowlist():
    data = payload()
    data['repositories']['repo0'].update(rx_bytes=2**64-2, tx_bytes=2**64-2)
    _, state = tick(data=data)
    data['repositories']['repo0'].update(rx_bytes=2**64-1, tx_bytes=2**64-1)
    rows, state = tick(state, data, 2)
    assert rows['repo0']['sync_rx_bps'] == 1
    for field in ('uncertain', 'active_uncertain'):
        dirty = payload(); dirty['repositories']['repo0'][field] = 1
        _, state = tick(data=dirty)
        rows, state = tick(state, payload(), 2)
        assert rows['repo0']['sync_rx_bps'] is None
        rows, state = tick(state, payload(), 3)
        assert rows['repo0']['sync_rx_bps'] == 0
    assert set(state) == {'epoch', 'identity', 'monotonic', 'repositories'}
    assert set(state['identity']) == {'master', 'workers', 'boot'}
    assert 'generation1' not in json.dumps(state)


def test_strict_types_for_every_counter_and_worker_field():
    _, previous = tick()
    for field in ('rx_bytes', 'tx_bytes', 'uncertain', 'active_uncertain'):
        for invalid in (True, -1, 0.0, 2**64, None, {}, []):
            data = payload(); data['repositories']['repo0'][field] = invalid
            assert tick(previous, data, 2)[1] == {}, (field, invalid)
    for field, values in {'schema_version': [True, 1.0, 2, None],
                          'epoch': ['', 1, None, 'x'*257],
                          'workers': [[True], [11.0], [0], [-1], [2**31], [], [11]*1025]}.items():
        for invalid in values:
            data = payload(); data[field] = invalid
            assert tick(previous, data, 2)[1] == {}, (field, invalid)
    raw = json.dumps(payload()).replace('"rx_bytes": 100', '"rx_bytes": 100, "rx_bytes": 100', 1).encode()
    rows, state = module().sample(CONFIG, previous, reader=lambda: raw,
                                   workers_reader=lambda root: IDENTITY)
    assert state == {}


def test_missing_default_endpoint_is_unknown_without_extra_config():
    rows, state = module().sample(CONFIG, {}, now=1000, proc_root='/not-present')
    assert state == {} and all(row['rates_observed_at'] is None for row in rows.values())
