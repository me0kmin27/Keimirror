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

class DeadlineTests(unittest.TestCase):
    def test_deadlines_reserve_recovery_without_extension_on_resume(self):
        from unittest.mock import patch
        m = load()
        self.assertTrue(hasattr(m, 'Deadlines'), 'absolute deadline controller missing')
        saved = {}; now = [100.0]
        def save(j): saved.clear(); saved.update(j)
        with patch.object(m.time, 'monotonic', side_effect=lambda: now[0]), patch.object(m.signal, 'setitimer'):
            d = m.Deadlines({}, save)
            self.assertEqual(d.hard_end, 550)
            d.phase('copy'); self.assertEqual(d.deadline, 130)
            now[0] = 129; d.phase('workers'); self.assertEqual(d.deadline, 189)
            now[0] = 369; d.phase('adoption'); self.assertEqual(d.deadline, 370)
            now[0] = 370; d.phase('recovery'); self.assertEqual(d.deadline, 550)
            now[0] = 400
            resumed = m.Deadlines(saved.copy(), save)
            resumed.phase('recovery'); self.assertEqual(resumed.deadline, 550)
            now[0] = 551
            with self.assertRaises(TimeoutError): resumed.remaining()

    def test_expired_forward_can_resume_only_within_reserved_recovery(self):
        from unittest.mock import patch
        m = load(); now = [100.0]; saved = {}
        with patch.object(m.time, 'monotonic', side_effect=lambda: now[0]), patch.object(m.signal, 'setitimer'):
            d = m.Deadlines({'phase': 'winch-intent'}, lambda j: saved.update(j))
            now[0] = 400
            resumed = m.Deadlines(saved, lambda j: None)
            resumed.phase('recovery')
            self.assertEqual(resumed.deadline, 550)
            with self.assertRaisesRegex(ValueError, 'recovery'): resumed.phase('workers')
            now[0] = 551
            with self.assertRaises(TimeoutError): m.Deadlines(saved, lambda j: None)

    def test_blocking_work_is_interrupted_by_real_alarm(self):
        import subprocess, sys
        m = load()
        self.assertTrue(hasattr(m, 'Deadlines'), 'absolute deadline controller missing')
        code = f'''import importlib.util,time,signal
s=importlib.util.spec_from_file_location('tx',{str(PATH)!r});m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
signal.signal(signal.SIGALRM,m.deadline_expired)
m.BUDGETS['copy']=0.05
d=m.Deadlines({{}},lambda j:None);d.phase('copy')
try: time.sleep(5)
except TimeoutError: print('deadline fired')
else: raise AssertionError('blocking work escaped deadline')
finally: signal.setitimer(signal.ITIMER_REAL,0)
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'deadline fired')

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
        b.verify_configs = lambda: None
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

class ReadOnlyConfigTests(unittest.TestCase):
    def test_exact_operator_allowlist_is_readonly_and_detects_drift(self):
        import tempfile, os
        from unittest.mock import patch
        m = load()
        self.assertTrue(hasattr(m, 'readonly_config'), 'exact readonly config policy missing')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'asn.conf'; p.write_bytes(b'# operator config\n'); p.chmod(0o644)
            frozen = m.identity_snapshot(p)
            # Only ancestor trust is substituted: fixture lives in a user scratch tree.
            with patch.object(m, 'trusted') as trust:
                self.assertEqual(m.readonly_config(p, {str(p): frozen}), frozen)
                trust.assert_called_with(p.parent)
                p.write_bytes(b'# changed\n')
                with self.assertRaises(ValueError): m.readonly_config(p, {str(p): frozen})
                p.write_bytes(b'# operator config\n')
                changed = dict(m.identity_snapshot(p), uid=frozen['uid'] + 1)
                with self.assertRaises(ValueError): m.readonly_config(p, {str(p): changed})
                p.chmod(0o666)
                with self.assertRaises(ValueError): m.readonly_config(p, {str(p): m.identity_snapshot(p)})
                p.chmod(0o644)
                frozen = m.identity_snapshot(p)
                target = Path(d) / 'other'; p.rename(target); p.symlink_to(target)
                with self.assertRaises((ValueError, OSError)): m.readonly_config(p, {str(p): frozen})

    def test_exact_nonroot_parent_chain_is_only_readonly_exception(self):
        import tempfile, os, stat
        m = load()
        # /tmp is deliberately rejected by the production ancestor policy.
        # Use the caller's private scratch directory, or the CI user's home.
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or Path.home()) as d:
            p = Path(d) / 'asn.conf'; p.write_text('# existing operator authority')
            parents = {str(a): dict(uid=a.lstat().st_uid, gid=a.lstat().st_gid,
                       mode=stat.S_IMODE(a.lstat().st_mode), dev=a.lstat().st_dev,
                       ino=a.lstat().st_ino) for a in p.parents}
            frozen = dict(m.identity_snapshot(p), parents=parents)
            self.assertEqual(m.readonly_config(p, {str(p): frozen}), m.identity_snapshot(p))
            with self.assertRaises(ValueError): m.trusted(p)
            Path(d).chmod(0o777)
            with self.assertRaises(ValueError): m.readonly_config(p, {str(p): frozen})

    def test_nonallowlisted_config_and_execution_inputs_keep_root_policy(self):
        import tempfile
        m = load()
        self.assertTrue(hasattr(m, 'readonly_config'), 'exact readonly config policy missing')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'load.conf'; p.write_bytes(b'load_module candidate;')
            with self.assertRaises(ValueError): m.readonly_config(p, {})
            with self.assertRaises(ValueError): m.trusted(p)

class PreservationTests(unittest.TestCase):
    def test_frozen_config_checked_before_signal_and_after_completion(self):
        from unittest.mock import Mock, patch
        m = load(); b = m.Linux.__new__(m.Linux)
        b.old = [1, 10]; b.m = {'old': {'cgroup': 'same'}}; b.s = {'binary': '/nginx'}
        b.remaining = lambda: 5
        b.verify_configs = Mock(side_effect=ValueError('readonly drift'))
        with patch.object(m, 'signal_identity') as send:
            with self.assertRaisesRegex(ValueError, 'readonly drift'): b.signal([1, 10], 'USR2')
            send.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'readonly drift'): b.finish()

    def test_unrelated_loadconf_drift_blocks_signal(self):
        import tempfile
        from unittest.mock import patch
        m = load(); b = m.Linux.__new__(m.Linux)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'load.conf'; p.write_bytes(b'original')
            original = m.snapshot(p)
            b.s = dict(config=str(p), load=str(p))
            b.m = dict(files={str(p): original}, config_links={}, preserved_configs={},
                       original=m.digest(b'original'), target=m.digest(b'candidate'))
            p.write_bytes(b'unrelated')
            with patch.object(m, 'config_inventory', return_value={str(p): m.snapshot(p)}), patch.object(m, 'trusted', side_effect=lambda path: Path(path)):
                with self.assertRaisesRegex(ValueError, 'loadconf'): b.verify_configs()

    def test_inventory_records_existing_symlink_identity(self):
        import tempfile
        m = load()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); conf = root / 'nginx.conf'; target = root / 'real.conf'
            target.write_text('events {}'); link = root / 'enabled.conf'; link.symlink_to(target)
            conf.write_text(f'include {link};')
            before = {}; after = {}
            m.config_inventory(conf, links=before)
            link.rename(root / 'retained-link'); link.symlink_to(target)
            m.config_inventory(conf, links=after)
            self.assertIn(str(link), before)
            self.assertNotEqual(before, after)

class LinuxBackendTests(unittest.TestCase):
    def test_prep_rejects_operator_owned_installer_before_stage_write(self):
        from unittest.mock import patch
        m = load()
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'trusted', side_effect=ValueError('untrusted root path')) as check:
            with self.assertRaises(ValueError): m.prepare({}, '/not-created/stage')
            self.assertEqual(str(check.call_args.args[0]), str(PATH))

    def test_production_cli_and_prep_are_implemented(self):
        m = load()
        self.assertTrue(hasattr(m, 'prepare'), 'root preparation missing')
        self.assertTrue(hasattr(m, 'Linux'), 'real Linux backend missing')
        self.assertTrue(hasattr(m, 'main'), 'root CLI missing')

class HarnessPolicyTests(unittest.TestCase):
    def test_isolated_harness_requires_root_staged_source_before_creating_unit_files(self):
        from unittest.mock import patch
        import sys
        m = load()
        spec = importlib.util.spec_from_file_location('isolated_reexec', PATH.with_name('isolated.py'))
        harness = importlib.util.module_from_spec(spec)
        gate_spec = importlib.util.spec_from_file_location('root_stage', PATH.with_name('root_stage.py'))
        gate = importlib.util.module_from_spec(gate_spec)
        gate_spec.loader.exec_module(gate)
        with patch.dict(sys.modules, {'root_stage': gate}): spec.loader.exec_module(harness)
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(gate, 'trusted', side_effect=ValueError('untrusted source')), patch.object(Path, 'mkdir') as mkdir:
            with self.assertRaisesRegex(ValueError, 'untrusted source'):
                harness.run(Path('/artifacts'), Path('/output'), 'forward')
            mkdir.assert_not_called()

class InstallerFailureTests(unittest.TestCase):
    def test_real_load_restore_on_copy_load_and_syntax_failure_never_writes_operator_config(self):
        import tempfile
        from unittest.mock import patch
        m = load()
        for failure in ('candidate-copy', 'load-replace', 'syntax'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                root = Path(d); stage = root / 'stage'; stage.mkdir()
                target = root / 'load.conf'; target.write_bytes(b'original'); target.chmod(0o644)
                operator = root / 'asn.conf'; operator.write_bytes(b'# preserved operator bytes')
                frozen = m.identity_snapshot(operator)
                (stage / 'candidate.so').write_bytes(b'module')
                (stage / 'load.candidate').write_bytes(b'candidate')
                (stage / 'load.original').write_bytes(b'original')
                b = Backend(); b.stage = stage
                b.s = dict(load=str(target), new_module=str(root / 'new.so'))
                b.m = dict(files={str(target): m.snapshot(target)}, target=m.digest(b'candidate'), original=m.digest(b'original'))
                b.remaining = lambda: 10
                b.verify_configs = lambda: self.assertEqual(m.identity_snapshot(operator), frozen)
                b.verify_backups = lambda: None
                b.install = lambda: m.Linux.install(b)
                b.restore = lambda: m.Linux.restore(b)
                fired = []; original_atomic = m.atomic
                def atomic(path, data, *args, **kwargs):
                    original_atomic(path, data, *args, **kwargs)
                    if not fired and ((failure == 'candidate-copy' and str(path) == b.s['new_module']) or
                                      (failure == 'load-replace' and path == target and data == b'candidate')):
                        fired.append(True); raise OSError('injected after durable write')
                def syntax():
                    if failure == 'syntax' and not fired:
                        fired.append(True); raise RuntimeError('syntax failed')
                b.syntax = syntax
                with patch.object(m, 'trusted', side_effect=lambda path, **kw: Path(path)), patch.object(m, 'atomic', side_effect=atomic), patch.object(m.os, 'fchown'):
                    with self.assertRaises((OSError, RuntimeError)): m.Transaction(b, b.saved).apply()
                self.assertTrue(fired, 'requested failure window was not reached')
                self.assertEqual(target.read_bytes(), b'original')
                self.assertEqual(m.snapshot(target), b.m['files'][str(target)])
                self.assertEqual(m.identity_snapshot(operator), frozen)
                self.assertEqual(b.saved['phase'], 'rolled-back')
                self.assertNotIn('old-USR2', b.events)

    def test_all_recovery_failures_keep_incomplete_journal(self):
        m = load()
        for failed in ('restore', 'syntax', 'old-HUP', 'old-ready', 'new-QUIT', 'new-drain', 'old-adopted', 'finish'):
            with self.subTest(failed=failed):
                b = Backend(failed); b.saved.update(phase='winch-intent', new=b.new)
                with self.assertRaises(RuntimeError): m.Transaction(b, b.saved).rollback()
                self.assertNotEqual(b.saved['phase'], 'rolled-back')
                if failed in ('restore', 'syntax', 'old-HUP', 'old-ready'):
                    self.assertNotIn('new-QUIT', b.events)
                self.assertNotIn('old-QUIT', b.events)

class InterruptionTests(unittest.TestCase):
    def test_every_forward_and_recovery_window_resumes_without_second_usr2(self):
        import tempfile, json
        m = load()
        class Interrupted(BaseException): pass
        windows = ['install', 'syntax', 'old-USR2', 'discover', 'new-ready', 'old-WINCH',
                   'accept', 'old-drain', 'old-QUIT', 'new-adopted', 'finish',
                   'restore', 'old-HUP', 'old-ready', 'new-QUIT', 'new-drain', 'old-adopted']
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'journal.json'
            for window in windows:
                for after in (False, True):
                    with self.subTest(window=window, after=after):
                        b = Backend(); original = b.step
                        b.save = lambda value: (setattr(b, 'saved', dict(value)), m.atomic(path, m.encoded(value)))
                        b.save(b.saved)
                        def interrupt(name, *args):
                            if name == window and not after: raise Interrupted()
                            original(name, *args)
                            if name == window and after: raise Interrupted()
                        b.step = interrupt
                        tx = m.Transaction(b, b.saved)
                        recovery = window in windows[11:]
                        if recovery:
                            tx.j.update(phase='winch-intent', new=b.new); b.save(tx.j)
                        with self.assertRaises(Interrupted):
                            tx.rollback() if recovery else tx.apply()
                        b.step = original
                        resumed = m.Transaction(b, json.loads(m.read(path)))
                        if resumed.j['phase'] == 'commit-intent':
                            with self.assertRaises(ValueError): resumed.rollback()
                        else:
                            resumed.rollback()
                            self.assertEqual(b.saved['phase'], 'rolled-back')
                        self.assertLessEqual(b.events.count('old-USR2'), 1)
                        self.assertNotIn('old-TERM', b.events); self.assertNotIn('new-KILL', b.events)

    def test_ambiguous_usr2_without_published_new_cannot_claim_rollback(self):
        m = load(); b = Backend(); b.saved['phase'] = 'usr2-intent'
        b.discover_optional = lambda: None
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            m.Transaction(b, b.saved).rollback()
        self.assertNotEqual(b.saved['phase'], 'rolled-back')
        self.assertNotIn('old-HUP', b.events)

    def test_resume_after_new_quit_does_not_hup_old_again(self):
        m = load(); b = Backend(); b.saved.update(phase='new-quit-intent', new=b.new)
        m.Transaction(b, b.saved).rollback()
        self.assertNotIn('old-HUP', b.events)
        self.assertLess(b.events.index('old-ready'), b.events.index('new-QUIT'))

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
