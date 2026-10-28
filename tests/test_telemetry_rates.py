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

    def sample(self, text, previous=None, now=100):
        self.assertIsNotNone(importlib.util.find_spec('telemetry.rates'), 'attributable sampler missing')
        rates = importlib.import_module('telemetry.rates')
        return rates.sample(self.config, previous, now=now, proc_root=self.proc, ss_reader=lambda: text)

    def test_ssl_ancestry_deltas_and_private_sanitization(self):
        self.process(11, ['rsync-ssl', 'host::repo', '/data/a'], parent=10)
        self.process(12, ['openssl', 's_client', '-connect', 'host:873'], parent=11)
        rows, old = self.sample(self.socket(pid=12))
        self.assertIsNone(rows['a']['sync_rx_bps'])
        rows, state = self.sample(self.socket(160, 500, pid=12), old, 110)
        self.assertEqual(rows['a'], {'sync_rx_bps': 30, 'sync_tx_bps': 6, 'rates_observed_at': 110})
        encoded = json.dumps(state)
        for secret in ('127.0.0.1', '/jobs', '/data', 'openssl', 'host', 'cgroup'):
            self.assertNotIn(secret, encoded)

    def test_wrong_attribution_and_ambiguous_ancestry_unknown(self):
        _, old = self.sample(self.socket())
        self.process(11, ['rsync', 'host::repo', '/data/b'], parent=10)
        rows, _ = self.sample(self.socket(150, 500), old, 110)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        self.assertIsNone(rows['b']['sync_rx_bps'])

    def test_counter_reset_missing_field_and_new_socket_are_unknown(self):
        _, old = self.sample(self.socket())
        for text in (self.socket(90, 500), self.socket(150, 500).replace('bytes_sent:150', ''),
                     self.socket(150, 500, cookie='a2'), self.socket(150, 500)+self.socket(pid=11, cookie='b1', inode=43)):
            with self.subTest(text=text):
                rows, _ = self.sample(text, old, 110)
                self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_shared_with_unattributed_process_is_unknown(self):
        self.process(20, ['curl', 'host'])
        text = self.socket().replace('fd=3))', 'fd=3),("curl",pid=20,fd=4))')
        _, old = self.sample(text)
        rows, _ = self.sample(text, old, 110)
        self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_uid_mismatch_in_socket_is_unknown(self):
        text = self.socket().replace(f'uid:{os.getuid()}', f'uid:{os.getuid()+1}')
        _, old = self.sample(text)
        rows, _ = self.sample(text, old, 110)
        self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_restarts_gaps_boot_reset_and_malformed_cache(self):
        _, old = self.sample(self.socket())
        for at in (100, 99, 131):
            self.assertIsNone(self.sample(self.socket(), old, at)[0]['a']['sync_rx_bps'])
        self.process(11, ['rsync', 'host::repo', '/data/a'], parent=10, ticks=51)
        self.assertIsNone(self.sample(self.socket(), old, 110)[0]['a']['sync_rx_bps'])
        (self.proc/'sys/kernel/random/boot_id').write_text('00000000-0000-4000-8000-000000000002')
        self.assertIsNone(self.sample(self.socket(), old, 110)[0]['a']['sync_rx_bps'])
        for bad in (None, [], {'boot': 1, 'at': 'bad'}, {'repositories': []}):
            self.assertIsNone(self.sample(self.socket(), bad, 110)[0]['a']['sync_rx_bps'])

    def test_serving_and_probe_descendants_excluded(self):
        for args in (['rsync', '--server', '/data/a'], ['rsync', '--daemon'], ['curl', 'host']):
            self.process(11, args, parent=10)
            _, old = self.sample(self.socket())
            rows, _ = self.sample(self.socket(), old, 110)
            self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_shared_socket_deduplicated_and_true_zero(self):
        self.process(12, ['ssh', 'host'], parent=11)
        text = self.socket().replace('fd=3))', 'fd=3),("ssh",pid=12,fd=4))')
        _, old = self.sample(text)
        rows, _ = self.sample(text.replace('bytes_received:200', 'bytes_received:500'), old, 110)
        self.assertEqual(rows['a']['sync_rx_bps'], 30)
        self.assertEqual(rows['a']['sync_tx_bps'], 0)

    def test_parse_failure_and_bounded_proc_reset(self):
        from telemetry import rates
        _, old = self.sample(self.socket())
        for text in ('nonsense', '\t bytes_sent:3', self.socket()+'broken\n'):
            self.assertEqual(self.sample(text, old, 110)[1], {})
        with mock.patch.object(rates, 'MAX_PROCESSES', 1):
            self.assertEqual(self.sample(self.socket(), old, 110)[1], {})

    def test_corrupt_extreme_timestamp_and_owner_cache_never_raise(self):
        _, old = self.sample(self.socket())
        old['at'] = 10**400
        self.assertIsNone(self.sample(self.socket(), old, 110)[0]['a']['sync_rx_bps'])

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
        rows, state = self.sample(self.socket(160, 500)+unknown, old, 110)
        self.assertEqual(rows['a'], {'sync_rx_bps': 30, 'sync_tx_bps': 6, 'rates_observed_at': 110})
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
                rows, state = self.sample(self.socket(160, 500)+unknown, old, 110)
                self.assertEqual(rows['a'], {'sync_rx_bps': 30, 'sync_tx_bps': 6,
                                             'rates_observed_at': 110})
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
                rows, state = self.sample(text, old, 110)
                self.assertEqual(state, {})
                self.assertIsNone(rows['a']['sync_rx_bps'])
                self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_ownerless_only_samples_never_publish_zero(self):
        text = self.socket(cookie='bb', inode=21727).replace('users:(("rsync",pid=11,fd=3)) ', '')
        _, old = self.sample(text)
        rows, state = self.sample(text, old, 110)
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
                rows, state = self.sample(text, old, 110)
                self.assertIsNone(rows['a']['sync_rx_bps'])
                self.assertEqual(rows['b']['sync_rx_bps'], 0)
                self.assertNotIn('a', state['repositories'])
                rows, _ = self.sample(remaining+sibling, state, 120)
                self.assertIsNone(rows['a']['sync_rx_bps'])

    def test_duplicate_cookie_with_unattributed_owner_invalidates_observed_repo(self):
        self.process(20, ['curl', 'host'])
        for conflicting in (self.socket(pid=20),
                            self.socket().replace('users:(("rsync",pid=11,fd=3)) ', '')):
            for text in (self.socket()+conflicting, conflicting+self.socket()):
                with self.subTest(text=text):
                    _, old = self.sample(text)
                    rows, state = self.sample(text, old, 110)
                    self.assertIsNone(rows['a']['sync_rx_bps'])
                    self.assertNotIn('a', state['repositories'])

    def test_ambiguous_owner_cookie_invalidates_all_affected_repos(self):
        self.process(20, ['rsync', 'host::repo', '/data/b'])
        self.process(21, ['curl', 'host'])
        ambiguous = self.socket(pid=20).replace('fd=3))', 'fd=3),("curl",pid=21,fd=4))')
        for text in (self.socket()+ambiguous, ambiguous+self.socket()):
            with self.subTest(text=text):
                _, old = self.sample(text)
                rows, state = self.sample(text, old, 110)
                for rid in ('a', 'b'):
                    self.assertIsNone(rows[rid]['sync_rx_bps'])
                    self.assertNotIn(rid, state['repositories'])

    def test_duplicate_cookie_with_conflicting_ownership_is_unknown(self):
        self.process(20, ['rsync', 'host::repo', '/data/b'])
        text = self.socket()+self.socket(pid=20)
        _, old = self.sample(text)
        rows, _ = self.sample(text, old, 110)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        self.assertIsNone(rows['b']['sync_rx_bps'])

    def test_ownership_change_during_ss_resets_coverage(self):
        from telemetry import rates
        _, old = self.sample(self.socket())
        def reader():
            self.process(11, ['rsync', 'host::repo', '/data/a'], parent=10, ticks=51)
            return self.socket()
        rows, state = rates.sample(self.config, old, now=110, proc_root=self.proc, ss_reader=reader)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        self.assertEqual(state, {})

    def test_scheduler_state_change_is_not_process_restart(self):
        from telemetry import rates
        _, old = self.sample(self.socket())
        def reader():
            path = self.proc/'11/stat'
            path.write_text(path.read_text().replace(' S ', ' R '))
            return self.socket(150, 500)
        rows, _ = rates.sample(self.config, old, now=110, proc_root=self.proc, ss_reader=reader)
        self.assertEqual(rows['a']['sync_rx_bps'], 30)

    def test_partial_proc_enumeration_never_returns_zero(self):
        _, old = self.sample(self.socket())
        (self.proc/'99').mkdir()
        rows, state = self.sample(self.socket(), old, 110)
        self.assertIsNone(rows['a']['sync_rx_bps'])
        self.assertEqual(state, {})


if __name__ == '__main__':
    unittest.main()
