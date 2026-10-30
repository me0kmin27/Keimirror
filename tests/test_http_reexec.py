"""Transaction regressions; privileged integration is explicitly opt-in."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'ops/telemetry/http-reexec/transaction.py'

def load():
    if not PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location('http_reexec', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PolicyTests(unittest.TestCase):
    def test_independent_budgets_reserve_recovery(self):
        m = load()
        self.assertIsNotNone(m, 'transaction implementation missing')
        self.assertEqual(m.BUDGETS['workers'], 60)
        self.assertEqual(m.BUDGETS['copy'], 30)
        self.assertGreaterEqual(m.TOTAL, sum(m.BUDGETS.values()))
        self.assertGreaterEqual(m.RECOVERY, 120)

class Backend:
    def __init__(self, failure=None):
        self.events = []
        self.failure = failure
        self.old = [100, 10]
        self.new = [200, 20]
        self.saved = {'phase': 'prepared', 'old': self.old, 'new': None}
    def save(self, value):
        self.saved = dict(value)
    def step(self, name, *args):
        self.events.append(name)
        if self.failure == name:
            raise RuntimeError(name)
    def preflight(self): self.step('preflight')
    def install(self): self.step('install')
    def syntax(self): self.step('syntax')
    def signal(self, who, sig): self.step(('old' if who == self.old else 'new') + '-' + sig)
    def discover(self): self.step('discover'); return self.new
    def ready(self, who): self.step(('old' if who == self.old else 'new') + '-ready')
    def accept(self): self.step('accept')
    def drain(self, who): self.step(('old' if who == self.old else 'new') + '-drain')
    def adopted(self, who): self.step(('old' if who == self.old else 'new') + '-adopted')
    def restore(self): self.step('restore')
    def discover_optional(self): return self.new
    def finish(self): self.step('finish')
    def phase(self, name): self.step('budget-' + name)

class IdentityTests(unittest.TestCase):
    def test_reused_pid_cannot_be_signaled(self):
        import os
        from unittest.mock import patch
        m = load()
        self.assertTrue(hasattr(m, 'signal_identity'), 'pidfd signal gate missing')
        with patch('signal.pidfd_send_signal') as send:
            with self.assertRaises(ValueError):
                m.signal_identity([os.getpid(), 1], 'QUIT')
            send.assert_not_called()

    def test_mapping_requires_inode_not_path_substring(self):
        import os, sys
        from unittest.mock import patch
        m = load()
        self.assertTrue(hasattr(m, 'mapping_matches'), 'mapping inode gate missing')
        path = str(Path(sys.executable).resolve())
        sha = m.digest(m.read(path))
        self.assertTrue(m.mapping_matches(os.getpid(), path, sha))
        original = m.read
        def counterfeit(p, *args):
            if str(p) == f'/proc/{os.getpid()}/maps':
                return f'1000-2000 r-xp 00000000 00:00 1 {path}\n'.encode()
            return original(p, *args)
        with patch.object(m, 'read', side_effect=counterfeit):
            self.assertFalse(m.mapping_matches(os.getpid(), path, sha))

    def test_interrupted_rollback_tolerates_already_exited_new(self):
        from unittest.mock import patch
        m = load(); b = m.Linux.__new__(m.Linux)
        b.old = [1, 10]; b.m = {'old': {'cgroup': 'same'}}
        b.s = {'binary': '/nginx'}
        b.remaining = lambda: 5
        with patch.object(m, 'process', side_effect=ProcessLookupError), patch.object(m, 'signal_identity') as send:
            b.signal([2, 20], 'QUIT')
            send.assert_not_called()

    def test_interrupted_file_restore_does_not_signal(self):
        m = load(); b = Backend()
        b.saved['phase'] = 'restore-files-intent'
        m.Transaction(b, b.saved).rollback()
        self.assertNotIn('old-HUP', b.events)

class FilePolicyTests(unittest.TestCase):
    def test_config_inventory_rejects_quic_and_forced_shutdown(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'nginx.conf'
            p.write_text('events {} http { server { listen 443 quic; } }')
            m = load()
            self.assertTrue(hasattr(m, 'config_inventory'), 'config preflight missing')
            with self.assertRaisesRegex(ValueError, 'QUIC'):
                m.config_inventory(p)
            p.write_text('worker_shutdown_timeout 10s; events {}')
            with self.assertRaisesRegex(ValueError, 'shutdown'):
                m.config_inventory(p)
            p.write_text('worker_shutdown_timeout 0; events {}')
            self.assertEqual(set(m.config_inventory(p)), {str(p)})

    def test_atomic_restore_preserves_original_metadata(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'load.conf'
            p.write_bytes(b'original'); p.chmod(0o664)
            m = load()
            self.assertTrue(hasattr(m, 'snapshot'), 'file transaction missing')
            old = m.snapshot(p)
            m.atomic(p, b'candidate', old)
            self.assertEqual(p.read_bytes(), b'candidate')
            m.atomic(p, b'original', old)
            self.assertEqual(m.snapshot(p), old)

class LinuxBackendTests(unittest.TestCase):
    def test_production_cli_and_prep_are_implemented(self):
        m = load()
        self.assertTrue(hasattr(m, 'prepare'), 'root preparation missing')
        self.assertTrue(hasattr(m, 'Linux'), 'real Linux backend missing')
        self.assertTrue(hasattr(m, 'main'), 'root CLI missing')

class TransactionTests(unittest.TestCase):
    def test_failure_before_usr2_restores_without_signals(self):
        m = load(); b = Backend('install')
        with self.assertRaises(RuntimeError):
            m.Transaction(b, b.saved).apply()
        self.assertIn('restore', b.events)
        self.assertNotIn('old-USR2', b.events)
        self.assertNotIn('old-HUP', b.events)
        self.assertEqual(b.saved['phase'], 'rolled-back')

    def test_old_not_ready_never_quits_new(self):
        m = load(); b = Backend('old-ready')
        b.saved.update(phase='winch-intent', new=b.new)
        with self.assertRaises(RuntimeError):
            m.Transaction(b, b.saved).rollback()
        self.assertNotIn('new-QUIT', b.events)
        self.assertNotEqual(b.saved['phase'], 'rolled-back')

    def test_interruption_discovers_new_before_recovery(self):
        m = load(); b = Backend()
        b.saved['phase'] = 'usr2-intent'
        m.Transaction(b, b.saved).rollback()
        self.assertEqual(b.saved['new'], b.new)
        self.assertLess(b.events.index('old-ready'), b.events.index('new-QUIT'))

    def test_commit_intent_never_rolls_back_dead_old(self):
        m = load(); b = Backend('new-adopted')
        with self.assertRaises(RuntimeError):
            m.Transaction(b, b.saved).apply()
        self.assertEqual(b.saved['phase'], 'commit-intent')
        self.assertNotIn('restore', b.events)
        with self.assertRaises(ValueError):
            m.Transaction(b, b.saved).rollback()
        self.assertNotIn('old-HUP', b.events)

    def test_failure_after_new_ready_recovers_old_first(self):
        m = load(); b = Backend('old-WINCH')
        with self.assertRaises(RuntimeError):
            m.Transaction(b, b.saved).apply()
        self.assertLess(b.events.index('old-ready'), b.events.index('new-QUIT'))

    def test_forward_and_failure_after_winch(self):
        m = load()
        self.assertTrue(hasattr(m, 'Transaction'), 'transaction state machine missing')
        b = Backend()
        m.Transaction(b, b.saved).apply()
        self.assertLess(b.events.index('new-ready'), b.events.index('old-WINCH'))
        self.assertLess(b.events.index('old-drain'), b.events.index('old-QUIT'))
        self.assertEqual(b.saved['phase'], 'committed')
        b = Backend('old-drain')
        with self.assertRaises(RuntimeError):
            m.Transaction(b, b.saved).apply()
        self.assertLess(b.events.index('restore'), b.events.index('old-HUP'))
        self.assertLess(b.events.index('old-ready'), b.events.index('new-QUIT'))
        self.assertNotIn('old-QUIT', b.events)
        self.assertEqual(b.saved['phase'], 'rolled-back')

if __name__ == '__main__':
    unittest.main()
