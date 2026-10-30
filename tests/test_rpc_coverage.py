"""Private proc/sysfs fixtures; never contacts production helpers."""
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from telemetry import rates


class CoverageTests(unittest.TestCase):
    def test_diag_endpoints_are_opt_in_and_match_real_loopback(self):
        import socket
        self.assertIn('endpoints', inspect.signature(rates.read_diag).parameters)
        with socket.socket() as listener, socket.socket() as client:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            client.connect(listener.getsockname())
            peer, _ = listener.accept()
            with peer:
                ino = os.fstat(client.fileno()).st_ino
                item, = [d for d in rates.read_diag(endpoints=True) if d['ino'] == ino]
                self.assertEqual(item['endpoint'], [2, '7f000001', '7f000001', client.getsockname()[1], listener.getsockname()[1]])
                self.assertNotIn('endpoint', next(d for d in rates.read_diag() if d['ino'] == ino))

    def setUp(self):
        patcher = mock.patch('telemetry.rpc.protect_memory')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'sys/kernel/random').mkdir(parents=True)
        (self.root/'sys/kernel/random/boot_id').write_text('00000000-0000-4000-8000-000000000001')
        (self.root/'self/ns').mkdir(parents=True)
        (self.root/'self/ns/net').symlink_to('net:[123]')
        self.config = {'repositories': {'a': {'script': '/job', 'user': __import__('pwd').getpwuid(os.getuid()).pw_name, 'path': '/data'}}}
        self.endpoint = [2, 'c0000201', 'c0000202', 900, 2049]
        self.diag = dict(ino=42, cookie=161, uid=0, state=1, rx=0, tx=0, endpoint=self.endpoint)
        self.rpc = [dict(endpoint=self.endpoint, inode=99, connected=True)]

    def take(self, previous=None, at=100, rpc=None, diag=None):
        self.assertIn('rpc_reader', inspect.signature(rates.sample).parameters,
                      'sampler lacks fresh RPC acquisition path')
        clock = iter(at + i/10000 for i in range(1000))
        with mock.patch.object(rates.time, 'monotonic', side_effect=lambda: next(clock)):
            return rates.sample(self.config, previous, now=at, proc_root=self.root,
                                ss_reader=lambda: '', diag_reader=diag or (lambda: [self.diag]),
                                rpc_reader=rpc or (lambda: self.rpc))

    def test_exact_rpc_exclusion_enables_two_sample_empty_coverage_without_leaks(self):
        rows, first = self.take()
        self.assertIsNone(rows['a']['sync_tx_bps'])
        rows, second = self.take(first, 101)
        self.assertEqual(rows['a']['sync_tx_bps'], 0)
        self.assertEqual(second['repositories'], {'a': {}})
        for private in ('endpoint', 'c0000201', '2049', 'connected'):
            self.assertNotIn(private, json.dumps([rows, first, second]))

    def test_production_sampler_acquires_live_helper_proof_not_cached_state(self):
        from telemetry import rpc
        def take(previous, at):
            clock = iter(at + i/10000 for i in range(1000))
            with mock.patch.object(rates.time, 'monotonic', side_effect=lambda: next(clock)):
                return rates.sample(self.config, previous, now=at, proc_root=self.root)
        with mock.patch.object(rates, 'read_ss', return_value=''), mock.patch.object(rates, 'read_diag', return_value=[self.diag]) as diag, mock.patch.object(rpc, 'read_snapshot', return_value=self.rpc) as reader:
            _, first = take(None, 100)
            rows, second = take(first, 101)
            self.assertEqual(rows['a']['sync_tx_bps'], 0, 'production never acquired helper proof')
            self.assertEqual(reader.call_count, 4)
            self.assertEqual(diag.call_count, 4)
            self.assertTrue(all(c.kwargs == {'endpoints': True} for c in diag.call_args_list))
            reader.side_effect = PermissionError('helper unavailable')
            rows, third = take(second, 102)
            self.assertIsNone(rows['a']['sync_tx_bps'])
            self.assertNotIn('a', third['repositories'])

    def test_fresh_sysfs_reader_and_missing_source_never_falls_back(self):
        from telemetry import rpc
        self.assertTrue(hasattr(rpc, 'read_transports'), 'bounded sysfs RPC reader missing')
        transport = self.root/'kernel/sunrpc/xprt-switches/switch-1/xprt-1-tcp'
        transport.mkdir(parents=True)
        (transport/'srcaddr').write_text('192.0.2.1\n')
        (transport/'dstaddr').write_text('192.0.2.2\n')
        (transport/'xprt_info').write_text('src_port=900\ndst_port=2049\n')
        (transport/'xprt_state').write_text('BOUND CONNECTED\n')
        result = rpc.read_transports(self.root)
        self.assertEqual(result, [dict(endpoint=self.endpoint, inode=transport.stat().st_ino, connected=True)])
        (transport/'xprt_info').write_text('src_port=901\ndst_port=2049\n')
        self.assertEqual(rpc.read_transports(self.root)[0]['endpoint'][-2], 901)
        with self.assertRaises(OSError):
            rpc.read_transports(self.root/'absent')

    def test_exclusion_negative_controls_and_previous_owner_invalidation(self):
        _, first = self.take()
        variants = [dict(self.diag, cookie=0), dict(self.diag, cookie=2**64-1),
                    dict(self.diag, uid=1000), dict(self.diag, state=2), dict(self.diag, ino=0),
                    dict(self.diag, endpoint=[2, 'c0000201', 'c0000202', 901, 2049])]
        for item in variants:
            with self.subTest(item=item):
                rows, _ = self.take(first, 101, diag=lambda: [item])
                self.assertIsNone(rows['a']['sync_tx_bps'])
        for changed in (dict(self.rpc[0], connected=False), dict(self.rpc[0], inode=100)):
            values = iter([self.rpc, [changed]])
            rows, _ = self.take(first, 101, rpc=lambda: next(values))
            self.assertIsNone(rows['a']['sync_tx_bps'])
        prior = dict(first, repositories={'a': {'161': dict(self.diag, owners=[[10, 50, 0]])}})
        rows, state = self.take(prior, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])
        self.assertNotIn('a', state['repositories'])
        fd = self.root/'10/fd'
        fd.mkdir(parents=True)
        (fd/'3').symlink_to('socket:[42]')
        # Direct proof scan, independent of ss's absent users field.
        from telemetry import rpc
        with mock.patch.object(rpc.time, 'monotonic', side_effect=[100, 100.1]):
            self.assertEqual(rpc.exclusions([self.diag], lambda: [self.diag], lambda: self.rpc, self.root), set())

    def test_private_ipc_live_exchange_rejects_wrong_namespace_and_stale_reply(self):
        import socket
        import threading
        from telemetry import rpc
        self.assertTrue(hasattr(rpc, 'read_snapshot'), 'authenticated private RPC IPC missing')
        transport = self.root/'kernel/sunrpc/xprt-switches/switch-1/xprt-1-tcp'
        transport.mkdir(parents=True)
        for name, value in {'srcaddr': '192.0.2.1', 'dstaddr': '192.0.2.2',
                            'xprt_info': 'src_port=900\ndst_port=2049', 'xprt_state': 'CONNECTED'}.items():
            (transport/name).write_text(value+'\n')
        self.rpc[0]['inode'] = transport.stat().st_ino
        path = self.root/'rpc.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
            listener.bind(str(path))
            path.chmod(0o600)
            listener.listen()
            errors = []
            def serve():
                try:
                    listener.settimeout(3)
                    for _ in range(5):
                        conn, _ = listener.accept()
                        with conn:
                            rpc.respond(conn, lambda: rpc.read_transports(self.root))
                except Exception as exc:
                    errors.append(type(exc).__name__)
            thread = threading.Thread(target=serve)
            with mock.patch.object(rpc, 'ROOT_UID', os.getuid()):
                thread.start()
                received = rpc.read_snapshot(path)
                _, first = self.take(rpc=lambda: rpc.read_snapshot(path))
                rows, _ = self.take(first, 101, rpc=lambda: rpc.read_snapshot(path))
                self.assertEqual(rows['a']['sync_tx_bps'], 0)
                thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(received, self.rpc)
        envelope = dict(nonce='abc', boot=1, netns='net:[123]', started=100.1,
                        finished=100.2, transports=self.rpc)
        self.assertEqual(rpc.validate_reply(envelope, 'abc', 1, 'net:[123]', 100, 100.3), self.rpc)
        for bad in (dict(envelope, nonce='old'), dict(envelope, boot=2),
                    dict(envelope, netns='net:[456]'), dict(envelope, started=99),
                    dict(envelope, finished=103), dict(envelope, finished=float('nan'))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                rpc.validate_reply(bad, 'abc', 1, 'net:[123]', 100, 100.3)
        with self.assertRaises(ValueError):
            rpc.validate_reply(envelope, 'abc', 1, 'net:[123]', 100, 102.001)

    def test_two_second_bound_cookie_inode_mismatch_and_visibility_gaps(self):
        from telemetry import rpc
        for elapsed in (0, 2.000001, 5):
            with self.subTest(elapsed=elapsed), mock.patch.object(rpc.time, 'monotonic', side_effect=[100, 100+elapsed]):
                with self.assertRaises(ValueError):
                    rpc.exclusions([self.diag], lambda: [self.diag], lambda: self.rpc, self.root)
        with mock.patch.object(rpc.time, 'monotonic', side_effect=[100, 102]):
            self.assertEqual(rpc.exclusions([self.diag], lambda: [self.diag], lambda: self.rpc, self.root), {(42, 161)})
        for changed in (dict(self.diag, cookie=162), dict(self.diag, ino=43),
                        dict(self.diag, state=8), dict(self.diag, uid=1)):
            with self.subTest(changed=changed), mock.patch.object(rpc.time, 'monotonic', side_effect=[100, 100.1]):
                self.assertEqual(rpc.exclusions([self.diag], lambda: [changed], lambda: self.rpc, self.root), set())
        (self.root/'10').mkdir()
        with self.assertRaises(OSError):
            rpc.holders(self.root)  # Missing FD inventory is not an empty inventory.

    def test_partial_http_is_never_reported_as_project_total_on_proof_loss(self):
        from telemetry.collector import combine_transfer_rates
        _, first = self.take()
        rows, second = self.take(first, 101)
        http = dict(sync_rx_bps=100, sync_tx_bps=500, rates_observed_at=101)
        self.assertEqual(combine_transfer_rates(rows['a'], http)['sync_tx_bps'], 500)
        def unavailable():
            raise PermissionError('no proof')
        rows, _ = self.take(second, 102, rpc=unavailable)
        self.assertIsNone(combine_transfer_rates(rows['a'], http)['sync_tx_bps'])
        rows, _ = self.take(second, 104)
        self.assertIsNone(rows['a']['sync_tx_bps'])
        for changed in (dict(first, boot=0), dict(first, netns=456)):
            rows, _ = self.take(changed, 101)
            self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_ipc_rejects_untrusted_peer_and_public_permissions(self):
        import socket
        from telemetry import rpc
        conn, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        with conn, peer:
            with mock.patch.object(rpc, 'ROOT_UID', os.getuid()+1), self.assertRaises(ValueError):
                rpc.peer_identity(conn)
        path = self.root/'rpc.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
            listener.bind(str(path))
            path.chmod(0o666)
            with mock.patch.object(rpc, 'ROOT_UID', os.getuid()), self.assertRaises(ValueError):
                rpc.read_snapshot(path)
