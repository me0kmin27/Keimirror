"""Actual children/ready/drain probes; no processes launched or signalled."""
import unittest
from unittest.mock import patch
from test_http_reexec import load


class ChurnTests(unittest.TestCase):
    def fixture(self, m):
        b = m.Linux.__new__(m.Linux)
        b.old = [100, 10]
        b.s = dict(old_module='/old', new_module='/new', binary='/nginx', workers=1)
        b.m = dict(old=dict(cgroup='same'), listeners=['900'])
        b.remaining = lambda: 1
        def proc(pid):
            return dict(identity=[pid, 10], parent=100, exe='/nginx', cgroup='same',
                        title='nginx: worker process' if pid != 100 else 'nginx: master process /nginx')
        return b, proc

    def test_actual_inventory_change_reacquires_ready_and_drain(self):
        for method in ('ready', 'drain'):
            m = load(); b, proc = self.fixture(m)
            snapshots = iter([b'201', b'', b'201' if method == 'ready' else b'', b'201' if method == 'ready' else b''])
            attempts = []
            def read(path, *args):
                if str(path).endswith('/children'):
                    attempts.append(path); return next(snapshots)
                return b'/old'
            with self.subTest(method=method), patch.object(m, 'process', side_effect=proc), patch.object(m, 'read', side_effect=read), patch.object(m, 'mapping_matches', return_value=True), patch.object(m, 'listeners', return_value=['900']), patch.object(m, 'sockets', return_value={'900'}), patch.object(m.time, 'sleep'):
                getattr(b, method)(b.old)
                self.assertEqual(len(attempts), 4)

    def test_child_disappearance_is_typed_but_invalid_evidence_is_not(self):
        m = load(); b, proc = self.fixture(m)
        for error in (FileNotFoundError(), ProcessLookupError(), PermissionError(), ValueError('hash mismatch')):
            def failing(pid):
                if pid == 201: raise error
                return proc(pid)
            with self.subTest(error=type(error)), patch.object(m, 'process', side_effect=failing), patch.object(m, 'read', return_value=b'201'):
                expected = m.ProcessSnapshotChurn if isinstance(error, (FileNotFoundError, ProcessLookupError)) else type(error)
                with self.assertRaises(expected): m.children(b.old)

    def test_actual_inventory_security_errors_are_never_churn(self):
        for change in ({'parent': 999}, {'exe': '/foreign'}, {'cgroup': 'foreign'}, {'title': 'unknown'}):
            m = load(); b, proc = self.fixture(m)
            def invalid(pid): return dict(proc(pid), **change) if pid == 201 else proc(pid)
            with self.subTest(change=change), patch.object(m, 'process', side_effect=invalid), patch.object(m, 'read', return_value=b'201'):
                with self.assertRaises(ValueError): m.children(b.old)
        for raw in (b'bad', b'-1', b'+201', b'0'):
            m = load(); b, proc = self.fixture(m)
            with self.subTest(raw=raw), patch.object(m, 'process', side_effect=proc), patch.object(m, 'read', return_value=raw):
                with self.assertRaises(ValueError): m.children(b.old)
        m = load(); b, proc = self.fixture(m)
        seen = []
        def reused(pid):
            p = proc(pid)
            if pid == 100:
                seen.append(pid)
                if len(seen) > 1: p['identity'] = [100, 99]
            return p
        with patch.object(m, 'process', side_effect=reused), patch.object(m, 'read', side_effect=[b'201', b'']):
            with self.assertRaisesRegex(ValueError, 'master identity changed'): m.children(b.old)

    def test_child_start_change_is_fatal_not_retryable(self):
        m = load(); b, proc = self.fixture(m)
        seen = []
        def reused(pid):
            p = proc(pid)
            if pid == 201:
                seen.append(pid)
                if len(seen) > 1: p['identity'] = [201, 99]
            return p
        with patch.object(m, 'process', side_effect=reused), patch.object(m, 'read', return_value=b'201'):
            with self.assertRaisesRegex(ValueError, 'child.*changed'): m.children(b.old)

    def test_ready_worker_start_reuse_is_fatal(self):
        m = load(); b, proc = self.fixture(m)
        def reused(pid):
            p = proc(pid)
            if pid == 201: p['identity'] = [201, 99]
            return p
        with patch.object(m, 'process', side_effect=reused), patch.object(m, 'children', return_value=[[201, 10]]), patch.object(m, 'read', return_value=b'/old'), patch.object(m, 'mapping_matches', return_value=True), patch.object(m, 'listeners', return_value=['900']), patch.object(m, 'sockets', return_value={'900'}):
            with self.assertRaisesRegex(ValueError, 'worker.*changed'): b.ready(b.old)

    def test_successful_snapshot_cannot_overrun_existing_deadline(self):
        m = load(); b, proc = self.fixture(m)
        remaining = iter([1, 0])
        def left():
            value = next(remaining)
            if not value: raise TimeoutError('existing deadline')
            return value
        b.remaining = left
        with patch.object(m, 'process', side_effect=proc), patch.object(m, 'children', return_value=[]):
            with self.assertRaises(TimeoutError): b.drain(b.old)

    def test_security_failures_immediate_and_churn_uses_existing_deadline(self):
        for method in ('ready', 'drain'):
            for error in (ValueError('malformed PID'), ValueError('hash'), ValueError('namespace'), PermissionError(), FileNotFoundError('non-child evidence'), None):
                m = load(); b, proc = self.fixture(m)
                remaining = iter([.1, .1, 0])
                def left():
                    value = next(remaining)
                    if not value: raise TimeoutError('existing deadline')
                    return value
                b.remaining = left
                failure = error if error is not None else m.ProcessSnapshotChurn('race')
                with self.subTest(method=method, error=failure), patch.object(m, 'process', side_effect=proc), patch.object(m, 'read', return_value=b'/old'), patch.object(m, 'mapping_matches', return_value=True), patch.object(m, 'children', side_effect=failure) as inv, patch.object(m.time, 'sleep'):
                    with self.assertRaises(type(error) if error is not None else TimeoutError): getattr(b, method)(b.old)
                    self.assertEqual(inv.call_count, 1)


if __name__ == '__main__':
    unittest.main()
