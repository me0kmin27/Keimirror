"""Isolated proc/TCP fixtures; no production traffic."""
import importlib
import importlib.util
from unittest import mock
import json
import os
from pathlib import Path
import pwd
import tempfile
import unittest


class RateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name)
        boot = self.proc / 'sys/kernel/random/boot_id'
        boot.parent.mkdir(parents=True)
        boot.write_text('00000000-0000-4000-8000-000000000001')
        user = pwd.getpwuid(os.getuid()).pw_name
        self.config = {'repositories': {r: {'user': user, 'script': '/jobs/'+r,
                                           'path': '/data/'+r} for r in ('a', 'b')}}
        self.process(10, ['/bin/sh', '/jobs/a'])
        self.process(11, ['rsync', 'host::repo', '/data/a'], parent=10)

    def process(self, pid, args, parent=0, ticks=50):
        d = self.proc / str(pid)
        d.mkdir(exist_ok=True)
        (d/'stat').write_text(f'{pid} (a tricky) name) S {parent} ' + '0 '*17 + str(ticks)+'\n')
        (d/'cmdline').write_bytes(('\0'.join(args)+'\0').encode())

    def socket(self, sent=100, received=200, pid=11, cookie='a1', inode=42):
        return (f'ESTAB 0 0 127.0.0.1:1234 127.0.0.1:4321 users:(("rsync",pid={pid},fd=3)) '
                f'uid:{os.getuid()} ino:{inode} sk:{cookie} cgroup:/synthetic\n'
                f'\t cubic bytes_sent:{sent} bytes_received:{received} segs_out:10\n')

    def sample(self, text, previous=None, now=100.0):
        self.assertIsNotNone(importlib.util.find_spec('telemetry.rates'), 'attributable sampler missing')
        rates = importlib.import_module('telemetry.rates')
        with mock.patch.object(rates.time, 'monotonic', return_value=now):
            return rates.sample(self.config, previous, now=now, proc_root=self.proc, ss_reader=lambda: text)

    def test_latest_sample_rejects_long_gap_and_recovers_without_smoothing(self):
        _, old = self.sample(self.socket())
        for elapsed in (2.001, 15, 30):
            with self.subTest(elapsed=elapsed):
                rows, state = self.sample(self.socket(160, 500), old, 100 + elapsed)
                self.assertEqual(rows['a'], {'sync_rx_bps': None, 'sync_tx_bps': None,
                                             'rates_observed_at': None})
                self.assertEqual(state['at'], 100 + elapsed)
                rows, _ = self.sample(self.socket(170, 520), state, 101 + elapsed)
                self.assertEqual(rows['a'], {'sync_rx_bps': 20, 'sync_tx_bps': 10,
                                             'rates_observed_at': 101 + elapsed})
        for elapsed in (0.75, 1, 1.25, 2):
            with self.subTest(elapsed=elapsed):
                rows, _ = self.sample(self.socket(160, 500), old, 100 + elapsed)
                self.assertEqual(rows['a']['sync_rx_bps'], 300 / elapsed)
                self.assertEqual(rows['a']['sync_tx_bps'], 60 / elapsed)

    def test_runtime_timestamp_is_taken_at_socket_observation(self):
        from telemetry import rates
        clock = [100.0]
        def reader():
            clock[0] += 0.25  # Sampling work is not a zero-duration operation.
            return self.socket()
        with mock.patch.object(rates.time, 'time', side_effect=lambda: clock[0]), mock.patch.object(rates.time, 'monotonic', side_effect=lambda: clock[0]):
            _, old = rates.sample(self.config, None, now=None, proc_root=self.proc, ss_reader=reader)
            self.assertEqual(old['at'], 100.25)
            clock[0] = 101.0
            rows, state = rates.sample(self.config, old, now=None, proc_root=self.proc,
                                       ss_reader=lambda: self.socket(160, 500))
        self.assertEqual(state['at'], 101.0)
        self.assertEqual(rows['a']['sync_rx_bps'], 400)
        self.assertEqual(rows['a']['sync_tx_bps'], 80)
        self.assertEqual(rows['a']['rates_observed_at'], 101.0)

    def test_monotonic_elapsed_survives_wall_clock_step_and_migrates(self):
        from telemetry import rates
        with mock.patch.object(rates.time, 'monotonic', side_effect=[10.0, 11.0]):
            _, old = rates.sample(self.config, None, now=100, proc_root=self.proc, ss_reader=lambda: self.socket())
            rows, state = rates.sample(self.config, old, now=50, proc_root=self.proc, ss_reader=lambda: self.socket(160, 500))
        self.assertEqual(rows['a']['sync_rx_bps'], 300)
        self.assertEqual(rows['a']['rates_observed_at'], 50)
        self.assertEqual(state['monotonic'], 11)
        legacy = dict(old)
        legacy.pop('monotonic')
        rows, _ = self.sample(self.socket(160, 500), legacy, now=101)
        self.assertIsNone(rows['a']['sync_rx_bps'])

    def delivery_fixture(self):
        from telemetry import rates
        module = self.proc/'module'
        module.mkdir()
        self.config['repositories']['a']['path'] = str(module)
        self.process(30, ['rsync', '--daemon'])
        self.process(31, ['rsync', '--daemon'], parent=30)
        (self.proc/'31/root').symlink_to(module)
        (self.proc/'31/cwd').symlink_to(module)
        (self.proc/'31/fd').mkdir()
        (self.proc/'31/fd/3').symlink_to('socket:[42]')
        cfg = self.proc/'rsyncd.conf'
        cfg.write_text('uid = nobody\nuse chroot = yes\n[a]\npath = '+str(module)+'\n')
        table = rates.processes(self.proc)
        table[30] = (*table[30][:2], 0, *table[30][3:])
        table[31] = (*table[31][:2], 65534, *table[31][3:])
        return cfg, table

    def test_verified_daemon_delivery_root_to_nobody(self):
        from telemetry import rates
        cfg, table = self.delivery_fixture()
        def take(sent, previous, now):
            text = self.socket(sent, 200, pid=31).replace(f'uid:{os.getuid()}', 'uid:0')
            with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda p: table[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, previous, now=now, proc_root=self.proc, ss_reader=lambda: text, rsync_config=cfg)
        self.assertIn('rsync_config', __import__('inspect').signature(rates.sample).parameters)
        _, old = take(100, None, 100)
        rows, state = take(600, old, 101)
        self.assertEqual(rows['a']['sync_tx_bps'], 500)
        self.assertEqual(rows['a']['sync_rx_bps'], 0)
        self.assertNotIn(str(self.proc), json.dumps(state))
        (self.proc/'31/fd/3').unlink()
        rows, _ = take(900, state, 102)
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_diag_supported_zero_and_missing_tcp_info(self):
        import struct
        from telemetry import rates
        self.assertTrue(hasattr(rates, 'decode_diag'), 'direct TCP_INFO decoder missing')
        msg = bytearray(72)
        msg[0:2] = bytes([2, 2])  # IPv4 SYN-SENT
        struct.pack_into('=Q', msg, 44, 161)
        struct.pack_into('=II', msg, 64, os.getuid(), 42)
        info = bytearray(248)
        full = bytes(msg) + struct.pack('=HH', 252, 2) + info
        self.assertEqual(rates.decode_diag(full), dict(ino=42, cookie=161, uid=os.getuid(), rx=0, tx=0, state=2))
        for data in (bytes(msg), bytes(msg)+struct.pack('=HH', 140, 2)+bytes(136)):
            result = rates.decode_diag(data)
            self.assertIsNone(result['rx'])
            self.assertIsNone(result['tx'])
        with self.assertRaises(ValueError):
            rates.decode_diag(full[:-1])

    def test_diag_dump_loopback_counters(self):
        import socket
        from telemetry import rates
        self.assertTrue(hasattr(rates, 'read_diag'), 'bounded INET_DIAG reader missing')
        with socket.socket() as listener, socket.socket() as client:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            client.connect(listener.getsockname())
            peer, _ = listener.accept()
            with peer:
                inode = os.fstat(client.fileno()).st_ino
                client.sendall(b'x'*37)
                self.assertEqual(peer.recv(37), b'x'*37)
                found = [r for r in rates.read_diag() if r['ino'] == inode]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]['tx'], 37)
                self.assertEqual(found[0]['rx'], 0)
                self.assertEqual(found[0]['uid'], os.getuid())

    def test_diag_sample_explicit_syn_zero_and_verified_empty(self):
        from telemetry import rates
        self.assertIn('diag_reader', __import__('inspect').signature(rates.sample).parameters)
        (self.proc/'self/ns').mkdir(parents=True)
        (self.proc/'self/ns/net').symlink_to('net:[123]')
        (self.proc/'11/ns').mkdir()
        (self.proc/'11/ns/net').symlink_to('net:[123]')
        (self.proc/'11/fd').mkdir()
        (self.proc/'11/fd/3').symlink_to('socket:[42]')
        text = self.socket(0, 0).replace('bytes_sent:0', '').replace('bytes_received:0', '').replace('ESTAB', 'SYN-SENT')
        diag = [dict(ino=42, cookie=161, uid=os.getuid(), rx=0, tx=0, state=2)]
        def take(text, diag, old, now):
            with mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc, ss_reader=lambda: text, diag_reader=lambda: diag)
        _, old = take(text, diag, None, 100)
        rows, state = take(text, diag, old, 101)
        self.assertEqual(rows['a']['sync_rx_bps'], 0)
        self.assertEqual(rows['a']['sync_tx_bps'], 0)
        rows, state = take('', [], state, 102)
        self.assertIsNone(rows['a']['sync_tx_bps'])  # departure loses final bytes
        rows, state = take('', [], state, 103)
        self.assertEqual(rows['a']['sync_tx_bps'], 0)  # observed-live, not whole-interval zero
        rows, _ = take(text, [dict(diag[0], rx=None, tx=None)], old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_final_verification_must_finish_within_sampling_deadline(self):
        from telemetry import rates
        (self.proc/'self/ns').mkdir(parents=True)
        (self.proc/'self/ns/net').symlink_to('net:[123]')
        (self.proc/'11/ns').mkdir()
        (self.proc/'11/ns/net').symlink_to('net:[123]')
        (self.proc/'11/fd').mkdir()
        (self.proc/'11/fd/3').symlink_to('socket:[42]')
        diag = [dict(ino=42, cookie=161, uid=os.getuid(), rx=200, tx=100, state=1)]
        with mock.patch.object(rates.time, 'monotonic', return_value=100):
            _, old = rates.sample(self.config, None, now=100, proc_root=self.proc,
                                 ss_reader=self.socket, diag_reader=lambda: diag)
        diag[0].update(rx=500, tx=160)
        real_process, real_bounded = rates.process, rates.bounded
        for stage in ('process', 'boot'):
            for delay in (0, 2, 3):
                with self.subTest(stage=stage, delay=delay):
                    clock, checks = [101.0], {'process': 0, 'boot': 0}
                    def verify_process(directory):
                        result = real_process(directory)
                        if directory.name == '11':
                            checks['process'] += 1
                            if stage == 'process' and checks['process'] == 2:
                                clock[0] += delay
                        return result
                    def verify_boot(path, limit):
                        result = real_bounded(path, limit)
                        if path.name == 'boot_id':
                            checks['boot'] += 1
                            if stage == 'boot' and checks['boot'] == 2:
                                clock[0] += delay
                        return result
                    with mock.patch.object(rates, 'process', side_effect=verify_process), mock.patch.object(rates, 'bounded', side_effect=verify_boot), mock.patch.object(rates.time, 'monotonic', side_effect=lambda: clock[0]):
                        rows, state = rates.sample(self.config, old, now=101, proc_root=self.proc,
                                                   ss_reader=self.socket, diag_reader=lambda: diag)
                    self.assertEqual(checks[stage], 2)
                    if delay <= 2:
                        self.assertEqual(rows['a'], dict(sync_rx_bps=300, sync_tx_bps=60,
                                                        rates_observed_at=101))
                    else:
                        for row in rows.values():
                            self.assertEqual(row, dict(sync_rx_bps=None, sync_tx_bps=None,
                                                       rates_observed_at=None))
                        self.assertEqual(state, {})

    def test_excluded_holder_change_during_ss_invalidates_empty_zero(self):
        from telemetry import rates
        (self.proc/'self/ns').mkdir(parents=True)
        (self.proc/'self/ns/net').symlink_to('net:[123]')
        (self.proc/'11/ns').mkdir()
        (self.proc/'11/ns/net').symlink_to('net:[123]')
        (self.proc/'11/fd').mkdir()
        (self.proc/'11/fd/3').symlink_to('socket:[42]')
        diag = [dict(ino=42, cookie=161, uid=os.getuid(), rx=200, tx=100, state=1)]
        def take(reader, old, now):
            with mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc,
                                    ss_reader=reader, diag_reader=lambda: diag)
        for args, ticks in ((['rsync', 'host::repo', '/data/a'], 50),
                            (['curl', 'host'], 51)):
            with self.subTest(args=args, ticks=ticks):
                self.process(11, ['curl', 'host'], parent=10)
                _, old = take(self.socket, None, 100)
                rows, _ = take(self.socket, old, 101)
                self.assertEqual(rows['a']['sync_tx_bps'], 0)
                def reader():
                    self.process(11, args, parent=10, ticks=ticks)
                    return self.socket()
                rows, state = take(reader, old, 101)
                for row in rows.values():
                    self.assertEqual(row, dict(sync_rx_bps=None, sync_tx_bps=None,
                                               rates_observed_at=None))
                self.assertEqual(state, {})

    def test_uid_boundary_dependencies_revalidated_without_becoming_owners(self):
        from telemetry import rates
        (self.proc/'self/ns').mkdir(parents=True)
        (self.proc/'self/ns/net').symlink_to('net:[123]')
        (self.proc/'11/ns').mkdir()
        (self.proc/'11/ns/net').symlink_to('net:[123]')
        (self.proc/'11/fd').mkdir()
        (self.proc/'11/fd/3').symlink_to('socket:[42]')
        self.process(10, ['/bin/sh', '/jobs/b'])
        self.process(99, ['unrelated'])
        initial = rates.processes(self.proc)
        initial[10] = (*initial[10][:2], os.getuid()+1, *initial[10][3:])
        diag = [dict(ino=42, cookie=161, uid=os.getuid(), rx=200, tx=100, state=1)]
        live = dict(initial)
        def take(reader, old, now):
            with mock.patch.object(rates, 'processes', side_effect=lambda root: dict(live)), mock.patch.object(rates, 'process', side_effect=lambda p: live[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc,
                                    ss_reader=reader, diag_reader=lambda: diag)
        _, old = take(self.socket, None, 100)
        self.assertEqual(old['repositories']['a']['161']['owners'], [[11, 50, os.getuid()]])
        diag[0].update(rx=500, tx=160)
        rows, _ = take(self.socket, old, 101)
        self.assertEqual(rows['a']['sync_rx_bps'], 300)
        self.assertEqual(rows['b']['sync_rx_bps'], 0)
        for change in ('uid', 'argv', 'startticks', 'parent', 'dead', 'unrelated'):
            with self.subTest(change=change):
                live = dict(initial)
                def reader():
                    pid = 99 if change == 'unrelated' else 10
                    changed = list(live[pid])
                    index, value = {'uid': (2, os.getuid()), 'argv': (3, ['/bin/sh', '/jobs/a']),
                                    'startticks': (1, 51), 'parent': (0, 99),
                                    'dead': (4, 'Z'), 'unrelated': (1, 51)}[change]
                    changed[index] = value
                    live[pid] = tuple(changed)
                    return self.socket()
                rows, state = take(reader, old, 101)
                if change == 'unrelated':
                    self.assertEqual(rows['a']['sync_rx_bps'], 300)
                    self.assertEqual(rows['b']['sync_rx_bps'], 0)
                else:
                    for row in rows.values():
                        self.assertEqual(row, dict(sync_rx_bps=None, sync_tx_bps=None,
                                                   rates_observed_at=None))
                    self.assertEqual(state, {})

    def test_ownerless_diag_blocks_only_empty_zero_not_known_rates(self):
        from telemetry import rates
        (self.proc/'self/ns').mkdir(parents=True)
        (self.proc/'self/ns/net').symlink_to('net:[123]')
        unknown = dict(ino=55, cookie=99, uid=0, rx=0, tx=0, state=1)
        with mock.patch.object(rates.time, 'monotonic', return_value=100):
            _, old = rates.sample(self.config, None, now=100, proc_root=self.proc, ss_reader=lambda: '', diag_reader=lambda: [unknown])
        with mock.patch.object(rates.time, 'monotonic', return_value=101):
            rows, _ = rates.sample(self.config, old, now=101, proc_root=self.proc, ss_reader=lambda: '', diag_reader=lambda: [unknown])
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_delivery_boundary_dependencies_revalidated_without_becoming_owners(self):
        from telemetry import rates
        cfg, initial = self.delivery_fixture()
        initial[30] = (29, *initial[30][1:])
        initial[29] = (0, 50, 0, ['/bin/sh'], 'S')
        initial[99] = (0, 50, 0, ['unrelated'], 'S')
        for pid in ('self', '31'):
            (self.proc/pid/'ns').mkdir(parents=True)
            (self.proc/pid/'ns/net').symlink_to('net:[123]')
        diag = [dict(ino=42, cookie=161, uid=0, rx=200, tx=100, state=1)]
        live = dict(initial)
        def take(reader, old, now):
            with mock.patch.object(rates, 'processes', side_effect=lambda root: dict(live)), mock.patch.object(rates, 'process', side_effect=lambda p: live[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc,
                                    ss_reader=reader, diag_reader=lambda: diag, rsync_config=cfg)
        reader = lambda: self.socket(pid=31)
        _, old = take(reader, None, 100)
        self.assertEqual([i[0] for i in old['repositories']['a']['161']['owners']], [30, 31])
        diag[0].update(rx=500, tx=160)
        rows, _ = take(reader, old, 101)
        self.assertEqual(rows['a']['sync_tx_bps'], 60)
        for change in ('argv', 'uid', 'startticks', 'parent', 'dead', 'unrelated'):
            with self.subTest(change=change):
                live = dict(initial)
                def changed_reader():
                    pid = 99 if change == 'unrelated' else 29
                    changed = list(live[pid])
                    index, value = {'argv': (3, ['rsync', '--daemon']), 'uid': (2, 65534),
                                    'startticks': (1, 51), 'parent': (0, 99),
                                    'dead': (4, 'Z'), 'unrelated': (1, 51)}[change]
                    changed[index] = value
                    live[pid] = tuple(changed)
                    return reader()
                rows, state = take(changed_reader, old, 101)
                if change == 'unrelated':
                    self.assertEqual(rows['a']['sync_tx_bps'], 60)
                    self.assertEqual(rows['b']['sync_tx_bps'], 0)
                else:
                    for row in rows.values():
                        self.assertEqual(row, dict(sync_rx_bps=None, sync_tx_bps=None,
                                                   rates_observed_at=None))
                    self.assertEqual(state, {})

    def test_delivery_root_inode_change_resets_identity(self):
        from telemetry import rates
        cfg, table = self.delivery_fixture()
        text = self.socket(pid=31).replace(f'uid:{os.getuid()}', 'uid:0')
        def take(old, now):
            with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda p: table[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc, ss_reader=lambda: text, rsync_config=cfg)
        _, old = take(None, 100)
        (self.proc/'module').rename(self.proc/'old-module')
        (self.proc/'module').mkdir()
        rows, _ = take(old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_delivery_root_change_during_observation_is_unknown(self):
        from telemetry import rates
        cfg, table = self.delivery_fixture()
        text = self.socket(pid=31).replace(f'uid:{os.getuid()}', 'uid:0')
        with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda p: table[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=100):
            _, old = rates.sample(self.config, None, now=100, proc_root=self.proc, ss_reader=lambda: text, rsync_config=cfg)
        (self.proc/'31/root').unlink()
        (self.proc/'31/root').symlink_to('/')
        def reader():
            (self.proc/'31/root').unlink()
            (self.proc/'31/root').symlink_to(self.proc/'module')
            return text
        with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda p: table[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=101):
            rows, _ = rates.sample(self.config, old, now=101, proc_root=self.proc, ss_reader=reader, rsync_config=cfg)
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_listening_socket_is_never_transfer_payload(self):
        text = self.socket().replace('ESTAB', 'LISTEN')
        _, old = self.sample(text)
        rows, _ = self.sample(text, old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_diag_dump_rejects_incomplete_error_and_bounds(self):
        import socket
        import struct
        from telemetry import rates
        def frame(kind=3, flags=2, seq=1, body=bytes(4)):
            return struct.pack('=IHHII', 16+len(body), kind, flags, seq, 0)+body
        cases = [(frame(seq=9), 0), (frame(flags=0x12), 0),
                 (frame(kind=2), 0), (frame(body=b'\xff'*4), 0),
                 (frame(), socket.MSG_TRUNC), (b'bad', 0)]
        for packet, flags in cases:
            with self.subTest(packet=packet, flags=flags):
                fake = mock.MagicMock()
                fake.__enter__.return_value = fake
                fake.recvmsg.return_value = (packet, [], flags, (0, 0))
                with mock.patch.object(socket, 'socket', return_value=fake):
                    with self.assertRaises(ValueError):
                        rates.read_diag()
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.recvmsg.side_effect = TimeoutError('no DONE')
        with mock.patch.object(socket, 'socket', return_value=fake):
            with self.assertRaises(TimeoutError):
                rates.read_diag()
        fake.recvmsg.side_effect = None
        fake.recvmsg.return_value = (frame(), [], 0, (0, 0))
        with mock.patch.object(socket, 'socket', return_value=fake), mock.patch.object(rates, 'MAX_OUTPUT', 8):
            with self.assertRaises(ValueError):
                rates.read_diag()

    def test_delivery_canonical_alias_and_rejected_credentials_roots(self):
        from telemetry import rates
        cfg, table = self.delivery_fixture()
        module = self.proc/'module'
        alias = self.proc/'alias'
        alias.symlink_to(module)
        cfg.write_text('[a]\npath = '+str(alias)+'\n')
        jobs = [('a', '/jobs/a', os.getuid(), {str(module)})]
        text = self.socket(pid=31).replace(f'uid:{os.getuid()}', 'uid:0')
        owners, chain = rates.delivery(31, table, jobs, self.proc, text, cfg)
        self.assertEqual(owners, {'a'})
        self.assertTrue(chain)
        for uid in (1, 1000, 65533):
            owners, chain = rates.delivery(31, table, jobs, self.proc, text.replace('uid:0', 'uid:'+str(uid)), cfg)
            self.assertFalse(chain)
        for kind, target in (('cwd', '/'), ('root', '/')):
            link = self.proc/'31'/kind
            link.unlink()
            link.symlink_to(target)
            owners, chain = rates.delivery(31, table, jobs, self.proc, text, cfg)
            self.assertFalse(chain)
            link.unlink()
            link.symlink_to(module)
        (self.proc/'31/fd/3').unlink()
        (self.proc/'31/fd/3').symlink_to('socket:[99]')
        self.assertFalse(rates.delivery(31, table, jobs, self.proc, text, cfg)[1])

    def test_unassigned_daemon_transfer_never_creates_false_repo_zero(self):
        from telemetry import rates
        cfg, table = self.delivery_fixture()
        (self.proc/'31/root').unlink()
        (self.proc/'31/root').symlink_to('/')
        text = self.socket(0, 0) + self.socket(pid=31, cookie='b1', inode=43).replace(f'uid:{os.getuid()}', 'uid:0')
        def take(old, now):
            with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda p: table[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc, ss_reader=lambda: text, rsync_config=cfg)
        _, old = take(None, 100)
        rows, _ = take(old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_unverified_module_root_does_not_hide_delivery_from_zero(self):
        from telemetry import rates
        cfg, table = self.delivery_fixture()
        cfg.write_text('[a]\npath = '+str(self.proc/'module')+'\nuse chroot = no\n')
        text = self.socket(0, 0) + self.socket(pid=31, cookie='b1').replace(f'uid:{os.getuid()}', 'uid:0')
        def take(old, now):
            with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda p: table[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc, ss_reader=lambda: text, rsync_config=cfg)
        _, old = take(None, 100)
        rows, _ = take(old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_unexpected_delivery_uid_is_unknown_not_ignored(self):
        from telemetry import rates
        cfg, table = self.delivery_fixture()
        table[31] = (*table[31][:2], 1, *table[31][3:])
        text = self.socket(0, 0) + self.socket(pid=31, cookie='b1').replace(f'uid:{os.getuid()}', 'uid:0')
        def take(old, now):
            with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda p: table[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc, ss_reader=lambda: text, rsync_config=cfg)
        _, old = take(None, 100)
        rows, _ = take(old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_ssl_ancestry_deltas_and_private_sanitization(self):
        self.process(11, ['rsync-ssl', 'host::repo', '/data/a'], parent=10)
        self.process(12, ['openssl', 's_client', '-connect', 'host:873'], parent=11)
        rows, old = self.sample(self.socket(pid=12))
        self.assertIsNone(rows['a']['sync_rx_bps'])
        rows, state = self.sample(self.socket(160, 500, pid=12), old, 101)
        self.assertEqual(rows['a'], {'sync_rx_bps': 300, 'sync_tx_bps': 60, 'rates_observed_at': 101})
        encoded = json.dumps(state)
        for secret in ('127.0.0.1', '/jobs', '/data', 'openssl', 'host', 'cgroup'):
            self.assertNotIn(secret, encoded)

    def test_wrong_attribution_and_ambiguous_ancestry_unknown(self):
        _, old = self.sample(self.socket())
        self.process(11, ['rsync', 'host::repo', '/data/b'], parent=10)
        rows, _ = self.sample(self.socket(150, 500), old, 101)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        self.assertIsNone(rows['b']['sync_rx_bps'])

    def test_counter_reset_missing_field_and_new_socket_are_unknown(self):
        _, old = self.sample(self.socket())
        for text in (self.socket(90, 500), self.socket(150, 500).replace('bytes_sent:150', ''),
                     self.socket(150, 500, cookie='a2'), self.socket(150, 500)+self.socket(pid=11, cookie='b1', inode=43)):
            with self.subTest(text=text):
                rows, _ = self.sample(text, old, 101)
                self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_shared_with_unattributed_process_is_unknown(self):
        self.process(20, ['curl', 'host'])
        text = self.socket().replace('fd=3))', 'fd=3),("curl",pid=20,fd=4))')
        _, old = self.sample(text)
        rows, _ = self.sample(text, old, 101)
        self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_uid_mismatch_in_socket_is_unknown(self):
        text = self.socket().replace(f'uid:{os.getuid()}', f'uid:{os.getuid()+1}')
        _, old = self.sample(text)
        rows, _ = self.sample(text, old, 101)
        self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_restarts_gaps_boot_reset_and_malformed_cache(self):
        _, old = self.sample(self.socket())
        for at in (100, 99, 131):
            self.assertIsNone(self.sample(self.socket(), old, at)[0]['a']['sync_rx_bps'])
        self.process(11, ['rsync', 'host::repo', '/data/a'], parent=10, ticks=51)
        self.assertIsNone(self.sample(self.socket(), old, 101)[0]['a']['sync_rx_bps'])
        (self.proc/'sys/kernel/random/boot_id').write_text('00000000-0000-4000-8000-000000000002')
        self.assertIsNone(self.sample(self.socket(), old, 101)[0]['a']['sync_rx_bps'])
        for bad in (None, [], {'boot': 1, 'at': 'bad'}, {'repositories': []}):
            self.assertIsNone(self.sample(self.socket(), bad, 101)[0]['a']['sync_rx_bps'])

    def test_serving_and_probe_descendants_excluded(self):
        for args in (['rsync', '--server', '/data/a'], ['rsync', '--daemon'], ['curl', 'host']):
            self.process(11, args, parent=10)
            _, old = self.sample(self.socket())
            rows, _ = self.sample(self.socket(), old, 101)
            self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_shared_socket_deduplicated_and_true_zero(self):
        self.process(12, ['ssh', 'host'], parent=11)
        text = self.socket().replace('fd=3))', 'fd=3),("ssh",pid=12,fd=4))')
        _, old = self.sample(text)
        rows, _ = self.sample(text.replace('bytes_received:200', 'bytes_received:500'), old, 101)
        self.assertEqual(rows['a']['sync_rx_bps'], 300)
        self.assertEqual(rows['a']['sync_tx_bps'], 0)

    def test_parse_failure_and_bounded_proc_reset(self):
        from telemetry import rates
        _, old = self.sample(self.socket())
        for text in ('nonsense', '\t bytes_sent:3', self.socket()+'broken\n'):
            self.assertEqual(self.sample(text, old, 101)[1], {})
        with mock.patch.object(rates, 'MAX_PROCESSES', 1):
            self.assertEqual(self.sample(self.socket(), old, 101)[1], {})

    def test_corrupt_extreme_timestamp_and_owner_cache_never_raise(self):
        _, old = self.sample(self.socket())
        old['monotonic'] = 10**400
        self.assertIsNone(self.sample(self.socket(), old, 101)[0]['a']['sync_rx_bps'])

    def test_ss_reader_loopback_format_and_payload(self):
        import socket
        from telemetry import rates
        with socket.socket() as listener, socket.socket() as client:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            client.connect(listener.getsockname())
            accepted, _ = listener.accept()
            with accepted:
                client.sendall(b'x'*37)
                self.assertEqual(accepted.recv(37), b'x'*37)
                accepted.sendall(b'y'*19)
                self.assertEqual(client.recv(19), b'y'*19)
                text = rates.read_ss()
                records = rates.parse_ss(text)
                own = [r for r in records if f'pid={os.getpid()},' in r
                       and f':{listener.getsockname()[1]} ' in r]
                self.assertEqual(len(own), 2)
                for row in own:
                    for token in ('bytes_sent:', 'bytes_received:', 'ino:', 'sk:'):
                        self.assertIn(token, row)

    def test_ss_reader_bounds_output_and_timeout_and_reaps_child(self):
        import subprocess
        import sys
        import time
        from telemetry import rates
        real_popen = subprocess.Popen
        for program, limit in (("import time; time.sleep(5)", rates.MAX_OUTPUT),
                               ("import sys; sys.stdout.write('x'*10000); sys.stdout.flush()", 256)):
            children = []
            def launch(*args, **kwargs):
                child = real_popen([sys.executable, '-c', program], **kwargs)
                children.append(child)
                return child
            start = time.monotonic()
            with mock.patch.object(rates.subprocess, 'Popen', side_effect=launch), mock.patch.object(rates, 'MAX_OUTPUT', limit):
                with self.assertRaises(ValueError):
                    rates.read_ss()
            self.assertLess(time.monotonic()-start, 2.5)
            self.assertIsNotNone(children[0].poll())

    def test_unrelated_ownerless_socket_preserves_observed_job_rates(self):
        unknown = self.socket(cookie='bb', inode=21727).replace('users:(("rsync",pid=11,fd=3)) ', '')
        rows, old = self.sample(self.socket()+unknown)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        rows, state = self.sample(self.socket(160, 500)+unknown, old, 101)
        self.assertEqual(rows['a'], {'sync_rx_bps': 300, 'sync_tx_bps': 60, 'rates_observed_at': 101})
        self.assertIsNone(rows['b']['sync_rx_bps'])
        self.assertEqual(set(state['repositories']), {'a'})

    def test_fin_wait_rows_preserve_observed_job_rates(self):
        from telemetry import rates
        for status in ('FIN-WAIT-1', 'FIN-WAIT-2'):
            with self.subTest(status=status):
                unknown = self.socket(cookie='bb', inode=21727).replace(
                    'users:(("rsync",pid=11,fd=3)) ', '').replace('ESTAB', status)
                text = self.socket()+unknown
                self.assertEqual(len(rates.parse_ss(text)), 2)
                rows, old = self.sample(text)
                self.assertIsNone(rows['a']['sync_rx_bps'])
                rows, state = self.sample(self.socket(160, 500)+unknown, old, 101)
                self.assertEqual(rows['a'], {'sync_rx_bps': 300, 'sync_tx_bps': 60,
                                             'rates_observed_at': 101})
                self.assertIsNone(rows['b']['sync_rx_bps'])
                self.assertEqual(set(state['repositories']), {'a'})

    def test_malformed_socket_headers_still_reset_coverage(self):
        from telemetry import rates
        _, old = self.sample(self.socket())
        for header in ('FIN-WAIT-1 x 0 local peer', 'FIN-WAIT-2 0 0 local',
                       '1FIN-WAIT 0 0 local peer', 'not-a-header 0 0 local peer'):
            with self.subTest(header=header):
                text = self.socket()+header+'\n'
                with self.assertRaisesRegex(ValueError, 'unrecognized socket row'):
                    rates.parse_ss(text)
                rows, state = self.sample(text, old, 101)
                self.assertEqual(state, {})
                self.assertIsNone(rows['a']['sync_rx_bps'])
                self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_ownerless_only_samples_never_publish_zero(self):
        text = self.socket(cookie='bb', inode=21727).replace('users:(("rsync",pid=11,fd=3)) ', '')
        _, old = self.sample(text)
        rows, state = self.sample(text, old, 101)
        self.assertEqual(state['repositories'], {})
        for row in rows.values():
            self.assertEqual(row, {'sync_rx_bps': None, 'sync_tx_bps': None, 'rates_observed_at': None})

    def test_missing_owner_on_previously_known_cookie_resets_only_affected_repo(self):
        self.process(20, ['rsync', 'host::repo', '/data/b'])
        sibling = self.socket(pid=20, cookie='b1', inode=43)
        remaining = self.socket(cookie='a2', inode=44)
        _, old = self.sample(self.socket()+remaining+sibling)
        ownerless = self.socket().replace('users:(("rsync",pid=11,fd=3)) ', '')
        for text in (ownerless+remaining+sibling, self.socket()+ownerless+remaining+sibling):
            with self.subTest(text=text):
                rows, state = self.sample(text, old, 101)
                self.assertIsNone(rows['a']['sync_rx_bps'])
                self.assertEqual(rows['b']['sync_rx_bps'], 0)
                self.assertNotIn('a', state['repositories'])
                rows, _ = self.sample(remaining+sibling, state, 102)
                self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_duplicate_cookie_with_unattributed_owner_invalidates_observed_repo(self):
        self.process(20, ['curl', 'host'])
        for conflicting in (self.socket(pid=20),
                            self.socket().replace('users:(("rsync",pid=11,fd=3)) ', '')):
            for text in (self.socket()+conflicting, conflicting+self.socket()):
                with self.subTest(text=text):
                    _, old = self.sample(text)
                    rows, state = self.sample(text, old, 101)
                    self.assertIsNone(rows['a']['sync_rx_bps'])
                    self.assertNotIn('a', state['repositories'])

    def test_ambiguous_owner_cookie_invalidates_all_affected_repos(self):
        self.process(20, ['rsync', 'host::repo', '/data/b'])
        self.process(21, ['curl', 'host'])
        ambiguous = self.socket(pid=20).replace('fd=3))', 'fd=3),("curl",pid=21,fd=4))')
        for text in (self.socket()+ambiguous, ambiguous+self.socket()):
            with self.subTest(text=text):
                _, old = self.sample(text)
                rows, state = self.sample(text, old, 101)
                for rid in ('a', 'b'):
                    self.assertIsNone(rows[rid]['sync_rx_bps'])
                    self.assertNotIn(rid, state['repositories'])

    def test_duplicate_cookie_with_conflicting_ownership_is_unknown(self):
        self.process(20, ['rsync', 'host::repo', '/data/b'])
        text = self.socket()+self.socket(pid=20)
        _, old = self.sample(text)
        rows, _ = self.sample(text, old, 101)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        self.assertIsNone(rows['b']['sync_rx_bps'])

    def test_ownership_change_during_ss_resets_coverage(self):
        from telemetry import rates
        _, old = self.sample(self.socket())
        def reader():
            self.process(11, ['rsync', 'host::repo', '/data/a'], parent=10, ticks=51)
            return self.socket()
        rows, state = rates.sample(self.config, old, now=101, proc_root=self.proc, ss_reader=reader)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        self.assertEqual(state, {})

    def test_scheduler_state_change_is_not_process_restart(self):
        from telemetry import rates
        _, old = self.sample(self.socket())
        def reader():
            path = self.proc/'11/stat'
            path.write_text(path.read_text().replace(' S ', ' R '))
            return self.socket(150, 500)
        with mock.patch.object(rates.time, 'monotonic', return_value=101):
            rows, _ = rates.sample(self.config, old, now=101, proc_root=self.proc, ss_reader=reader)
        self.assertEqual(rows['a']['sync_rx_bps'], 300)

    def test_partial_proc_enumeration_never_returns_zero(self):
        _, old = self.sample(self.socket())
        (self.proc/'99').mkdir()
        rows, state = self.sample(self.socket(), old, 101)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        self.assertEqual(state, {})


if __name__ == '__main__':
    unittest.main()
