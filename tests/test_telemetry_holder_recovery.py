"""Verified owner recovery: isolated proc fixtures, no production traffic."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock
from tests import test_telemetry_rates as fixtures
from telemetry import rates


import unittest


class HolderRecoveryTests(unittest.TestCase):
    process = fixtures.RateTests.process
    socket = fixtures.RateTests.socket

    def setUp(self):
        fixtures.RateTests.setUp(self)
        for pid in ('self', '10', '11'):
            (self.proc/pid/'ns').mkdir(parents=True)
            (self.proc/pid/'ns/net').symlink_to('net:[123]')
        for pid in ('10', '11'):
            (self.proc/pid/'fd').mkdir()
        (self.proc/'11/fd/3').symlink_to('socket:[42]')
        self.diag = dict(ino=42, cookie=161, uid=os.getuid(), rx=200, tx=100,
                         state=1, endpoint=[2, '7f000001', '7f000001', 1234, 4321])

    def take(self, old=None, now=100, reader=None):
        text = self.socket().replace('users:(("rsync",pid=11,fd=3))', '')
        with mock.patch.object(rates.time, 'monotonic', return_value=now):
            return rates.sample(self.config, old, now=now, proc_root=self.proc,
                                ss_reader=lambda: text, diag_reader=reader or (lambda: [dict(self.diag)]))

    def test_real_loopback_isolated_proc_and_diag_runtime(self):
        unshare = shutil.which('unshare')
        if not unshare:
            self.skipTest('unshare unavailable')
        command = [unshare, '-Urnmpf', '--mount-proc']
        probe = subprocess.run(command + ['true'], capture_output=True, timeout=10)
        if probe.returncode:
            self.skipTest('isolated user/network/PID namespaces unavailable')
        result = subprocess.run(command + [sys.executable, str(Path(__file__).with_name('holder_recovery_runtime.py'))],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"state_transition_unknown": true', result.stdout)

    def test_missing_nginx_holder_recovers_adjacent_empty_zero(self):
        self.process(11, ['nginx: worker process'], parent=10)
        _, old = self.take()
        rows, state = self.take(old, 101)
        self.assertEqual(rows['a']['sync_tx_bps'], 0)
        self.assertEqual(state['repositories'], {'a': {}, 'b': {}})

    def test_root_nginx_requires_live_socket_proof_not_name_or_uid(self):
        self.process(11, ['nginx: worker process'], parent=10)
        table = rates.processes(self.proc)
        table[11] = (*table[11][:2], 0, *table[11][3:])
        self.diag['uid'] = 0
        with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda path: table[int(path.name)]):
            _, old = self.take()
            rows, _ = self.take(old, 101)
            self.assertEqual(rows['a']['sync_tx_bps'], 0)
            for second in ([], [dict(self.diag, state=8)], [dict(self.diag, cookie=162)]):
                sequence = iter(([dict(self.diag)], second))
                rows, state = self.take(old, 101, reader=lambda: next(sequence))
                self.assertIsNone(rows['a']['sync_tx_bps'])
                self.assertNotIn('a', state.get('repositories', {}))
            (self.proc/'11/fd/3').unlink()
            rows, state = self.take(old, 101)
            self.assertIsNone(rows['a']['sync_tx_bps'])
            self.assertNotIn('a', state.get('repositories', {}))

    def test_unrelated_holder_socket_uid_mismatch_is_unknown(self):
        self.process(11, ['nginx: worker process'], parent=10)
        self.diag['uid'] = os.getuid() + 1
        _, old = self.take()
        rows, state = self.take(old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])
        self.assertNotIn('a', state.get('repositories', {}))

    def test_recovered_upstream_uses_counters_and_synthetic_diag_record(self):
        for synthetic in (False, True):
            with self.subTest(synthetic=synthetic):
                original = self.socket
                if synthetic:
                    self.socket = lambda: ''
                self.diag.update(rx=200, tx=100)
                _, old = self.take()
                self.diag.update(rx=500, tx=160)
                rows, state = self.take(old, 101)
                self.assertEqual(rows['a']['sync_rx_bps'], 300)
                self.assertEqual(rows['a']['sync_tx_bps'], 60)
                self.assertEqual(state['repositories']['a']['161']['owners'],
                                 [[10, 50, os.getuid()], [11, 50, os.getuid()]])
                self.socket = original

    def test_recovered_daemon_uses_existing_root_transition_classifier(self):
        cfg, table = fixtures.RateTests.delivery_fixture(self)
        for pid in ('30', '31'):
            (self.proc/pid/'ns').mkdir()
            (self.proc/pid/'ns/net').symlink_to('net:[123]')
        (self.proc/'30/fd').mkdir()
        (self.proc/'11/fd/3').unlink()
        self.diag['uid'] = 0
        def take(old, now):
            with mock.patch.object(rates, 'processes', return_value=table), mock.patch.object(rates, 'process', side_effect=lambda p: table[int(p.name)]), mock.patch.object(rates.time, 'monotonic', return_value=now):
                return rates.sample(self.config, old, now=now, proc_root=self.proc,
                                    ss_reader=lambda: '', diag_reader=lambda: [dict(self.diag)], rsync_config=cfg)
        _, old = take(None, 100)
        self.diag['tx'] += 700
        rows, _ = take(old, 101)
        self.assertEqual(rows['a']['sync_tx_bps'], 700)
        self.assertEqual(rows['a']['sync_rx_bps'], 0)

    def test_diagnostic_identity_loss_never_recovers(self):
        self.process(11, ['nginx: worker process'], parent=10)
        for change in ({'cookie': 162}, {'ino': 43}, {'state': 8}, {'uid': 12345},
                       {'endpoint': [2, '7f000001', '7f000001', 1234, 4322]}, None):
            with self.subTest(change=change):
                calls = []
                def reader():
                    calls.append(1)
                    return [dict(self.diag)] if len(calls) == 1 else ([] if change is None else [dict(self.diag, **change)])
                _, state = self.take(reader=reader)
                self.assertEqual(len(calls), 2)
                self.assertNotIn('a', state.get('repositories', {}))

    def test_holder_mutations_during_diag_never_recover(self):
        self.process(11, ['nginx: worker process'], parent=10)
        for mutation in ('pid', 'fd', 'namespace', 'uid'):
            with self.subTest(mutation=mutation):
                self.process(11, ['nginx: worker process'], parent=10)
                fd = self.proc/'11/fd/3'
                ns = self.proc/'11/ns/net'
                fd.unlink(); fd.symlink_to('socket:[42]')
                ns.unlink(); ns.symlink_to('net:[123]')
                real_process = rates.process
                changed = [False]
                def process(path):
                    value = real_process(path)
                    if changed[0] and mutation == 'uid' and path.name == '11':
                        return (*value[:2], value[2] + 1, *value[3:])
                    return value
                def reader():
                    if changed[0]:
                        if mutation == 'pid':
                            self.process(11, ['nginx: worker process'], parent=10, ticks=51)
                        elif mutation == 'fd':
                            fd.unlink(); fd.symlink_to('socket:[43]')
                        elif mutation == 'namespace':
                            ns.unlink(); ns.symlink_to('net:[124]')
                    changed[0] = True
                    return [dict(self.diag)]
                # UID change is visible already at inventory; other mutations at recheck.
                with mock.patch.object(rates, 'process', side_effect=process):
                    _, state = self.take(reader=reader)
                self.assertNotIn('a', state.get('repositories', {}))

    def test_shared_mixed_holders_invalidate_project_not_unrelated_zero(self):
        (self.proc/'10/fd/4').symlink_to('socket:[42]')
        self.process(10, ['nginx: worker process'])
        _, old = self.take()
        self.diag['tx'] += 60
        rows, state = self.take(old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])
        self.assertNotIn('a', state['repositories'])

    def test_scan_errors_do_not_suppress_already_known_socket(self):
        known = self.socket(inode=43, cookie='a2')
        diag_known = dict(self.diag, ino=43, cookie=162)
        (self.proc/'11/fd/3').unlink()
        (self.proc/'11/fd/3').symlink_to('socket:[43]')
        real_scandir = rates.os.scandir
        for error in (PermissionError('denied'), ValueError('bound')):
            def scandir(path):
                if str(path).endswith('/10/fd'):
                    raise error
                return real_scandir(path)
            def take(old, now):
                with mock.patch.object(rates.os, 'scandir', side_effect=scandir), mock.patch.object(rates.time, 'monotonic', return_value=now):
                    return rates.sample(self.config, old, now=now, proc_root=self.proc,
                                        ss_reader=lambda: known, diag_reader=lambda: [dict(self.diag), dict(diag_known)])
            _, old = take(None, 100)
            diag_known['tx'] += 60
            rows, state = take(old, 101)
            self.assertEqual(rows['a']['sync_tx_bps'], 60)
            self.assertNotIn('b', state['repositories'])

    def test_complete_ss_owners_never_need_recovery_scan(self):
        with mock.patch.object(rates, 'recover_holders', side_effect=AssertionError('unnecessary scan')), mock.patch.object(rates.time, 'monotonic', return_value=100):
            _, state = rates.sample(self.config, None, now=100, proc_root=self.proc,
                                    ss_reader=self.socket, diag_reader=lambda: [dict(self.diag)])
        self.assertIn('a', state['repositories'])

    def test_numeric_recovery_counts_are_private_and_project_scoped(self):
        from telemetry.private_reasons import Recorder
        import json
        for unrelated, code in ((False, '36'), (True, '37')):
            if unrelated:
                self.process(11, ['nginx: worker process'], parent=10)
            recorder = Recorder(self.config['repositories'])
            with mock.patch.object(rates.time, 'monotonic', return_value=100):
                rows, state = rates.sample(self.config, None, now=100, proc_root=self.proc,
                    ss_reader=lambda: '', diag_reader=lambda: [dict(self.diag)], diagnostic=recorder.sink('tcp'))
            counts = recorder.snapshot(100, state, {})['sources']['tcp']['repositories']
            self.assertEqual(counts.get('a', {}).get(code), 1)
            if unrelated:
                self.assertEqual(counts['b'][code], 1)
            self.assertNotIn('endpoint', json.dumps(state))
            self.assertNotIn('recovered', json.dumps(rows))

    def test_process_disappeared_before_inventory_is_incomplete(self):
        self.process(11, ['nginx: worker process'], parent=10)
        table = rates.processes(self.proc)
        table[99] = table[11]
        with mock.patch.object(rates, 'processes', return_value=table):
            rows, state = self.take()
        self.assertEqual(state, {})
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_ambiguous_same_inode_different_cookies_is_not_proof(self):
        self.process(11, ['nginx: worker process'], parent=10)
        for reader in (lambda: [dict(self.diag), dict(self.diag, cookie=162)],
                       iter(([dict(self.diag)], [dict(self.diag), dict(self.diag, cookie=162)])).__next__):
            _, state = self.take(reader=reader)
            self.assertNotIn('a', state.get('repositories', {}))

    def test_shared_scan_once_for_multiple_missing_sockets(self):
        self.process(11, ['nginx: worker process'], parent=10)
        (self.proc/'11/fd/4').symlink_to('socket:[43]')
        diag = [self.diag, dict(self.diag, ino=43, cookie=162)]
        with mock.patch.object(rates, 'recover_holders', wraps=rates.recover_holders) as scan:
            _, state = self.take(reader=lambda: diag)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(state['repositories'], {'a': {}, 'b': {}})

    def test_global_deadline_expires_in_scan_without_reset(self):
        clock = [100.0]
        real_scandir = rates.os.scandir
        scans = []
        def scandir(path):
            result = real_scandir(path)
            if str(path).endswith('/10/fd'):
                scans.append(1)
                clock[0] = 102.001
            return result
        with mock.patch.object(rates.os, 'scandir', side_effect=scandir), mock.patch.object(rates.time, 'monotonic', side_effect=lambda: clock[0]):
            rows, state = rates.sample(self.config, None, now=100, proc_root=self.proc,
                ss_reader=lambda: '', diag_reader=lambda: [dict(self.diag)])
        self.assertEqual(scans, [1])
        self.assertEqual(state, {})
        self.assertIsNone(rows['a']['sync_tx_bps'])

    def test_recovered_unrelated_does_not_erase_prior_cookie_loss(self):
        _, old = self.take()
        self.process(11, ['nginx: worker process'], parent=10)
        rows, state = self.take(old, 101)
        self.assertIsNone(rows['a']['sync_tx_bps'])
        self.assertNotIn('a', state['repositories'])

    def test_no_success_count_when_final_process_verification_fails(self):
        from telemetry.private_reasons import Recorder
        self.process(11, ['nginx: worker process'], parent=10)
        recorder = Recorder(self.config['repositories'])
        real_process = rates.process
        calls = []
        def process(path):
            result = real_process(path)
            if path.name == '11':
                calls.append(1)
                if len(calls) == 4:
                    return (result[0], result[1] + 1, *result[2:])
            return result
        with mock.patch.object(rates, 'process', side_effect=process), mock.patch.object(rates.time, 'monotonic', return_value=100):
            _, state = rates.sample(self.config, None, now=100, proc_root=self.proc,
                ss_reader=lambda: '', diag_reader=lambda: [dict(self.diag)], diagnostic=recorder.sink('tcp'))
        self.assertEqual(state, {})
        self.assertEqual(recorder.sources['tcp']['repositories'], {})

    def test_missing_fd_retries_whole_attempt_three_times(self):
        real_scandir = rates.os.scandir
        calls = []
        def scandir(path):
            if str(path).endswith('/10/fd'):
                calls.append(1)
                raise FileNotFoundError('churn')
            return real_scandir(path)
        with mock.patch.object(rates.os, 'scandir', side_effect=scandir):
            rows, state = self.take()
        self.assertEqual(len(calls), 3)
        self.assertEqual(state, {})
        self.assertIsNone(rows['a']['sync_tx_bps'])
