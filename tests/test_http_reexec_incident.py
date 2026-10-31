"""Incident regressions: real file IO, external process boundaries substituted."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_http_reexec import load, Backend


class PublicationTests(unittest.TestCase):
    def test_pid_writer_metadata_transition_is_retried_in_existing_deadline(self):
        m = load()
        b = m.Linux.__new__(m.Linux)
        b.old = [100, 10]
        b.s = dict(pid='', binary='/nginx')
        b.m = dict(old=dict(cgroup='same'))
        calls = []
        b.remaining = lambda: (calls.append('remaining') or 1)
        def process(pid):
            return dict(identity=[pid, 10 if pid == 100 else 20], parent=100,
                        uid=0, title='nginx: master process /nginx',
                        cgroup='same', exe='/nginx')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'nginx.pid'; p.write_bytes(b'200\n'); p.chmod(0o644)
            Path(str(p) + '.oldbin').write_bytes(b'100\n'); Path(str(p) + '.oldbin').chmod(0o644)
            b.s['pid'] = str(p)
            original = m.os.read
            raced = []
            def writer(fd, count):
                data = original(fd, count)
                if not raced:
                    raced.append(True)
                    p.write_bytes(b'200\n')
                return data
            # Only root ownership/ancestor policy is substituted for scratch.
            fstat = m.os.fstat
            def rootstat(fd):
                s = fstat(fd)
                from types import SimpleNamespace
                return SimpleNamespace(**{n: (0 if n in ('st_uid', 'st_gid') else getattr(s, n))
                    for n in dir(s) if n.startswith('st_')})
            with patch.object(m, 'trusted'), patch.object(m.os, 'fstat', side_effect=rootstat), patch.object(m.os, 'read', side_effect=writer), patch.object(m, 'process', side_effect=process), patch.object(m.time, 'sleep'):
                self.assertEqual(b.discover(), [200, 20])
            self.assertTrue(raced)
            self.assertGreater(len(calls), 1)


class RollbackTests(unittest.TestCase):
    def test_before_winch_proves_original_workers_without_old_hup(self):
        m = load(); b = Backend('new-ready')
        b.original_ready = lambda: b.step('original-ready')
        with self.assertRaisesRegex(RuntimeError, 'new-ready'):
            m.Transaction(b, b.saved).apply()
        self.assertNotIn('old-HUP', b.events)
        self.assertLess(b.events.index('original-ready'), b.events.index('new-QUIT'))
        self.assertEqual(b.saved['phase'], 'rolled-back')

    def test_partial_original_generation_cannot_quit_new(self):
        m = load(); b = Backend()
        b.saved.update(phase='new-discovered', new=b.new)
        def partial():
            raise ValueError('original workers changed')
        b.original_ready = partial
        with self.assertRaisesRegex(ValueError, 'original workers changed'):
            m.Transaction(b, b.saved).rollback()
        self.assertNotIn('old-HUP', b.events)
        self.assertNotIn('new-QUIT', b.events)
        self.assertNotEqual(b.saved['phase'], 'rolled-back')

    def test_loaded_nchan_rejected_by_readonly_config_gate(self):
        m = load()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'nginx.conf'
            p.write_text('load_module /modules/ngx_nchan_module.so; events {}')
            with self.assertRaisesRegex(ValueError, 'Nchan.*rollback'):
                m.config_inventory(p)

    def test_recovery_failure_preserves_original_error(self):
        m = load(); b = Backend('new-ready')
        b.original_ready = lambda: (_ for _ in ()).throw(ValueError('recovery failed'))
        with self.assertRaisesRegex(RuntimeError, 'new-ready') as caught:
            m.Transaction(b, b.saved).apply()
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertNotEqual(b.saved['phase'], 'rolled-back')


class SafetyTests(unittest.TestCase):
    def test_loaded_renamed_nchan_rejected_before_install(self):
        m = load(); b = m.Linux.__new__(m.Linux)
        b.old = [100, 10]
        b.verify_configs = lambda: None
        b.verify_backups = lambda: None
        b.s = {}; b.m = {}
        with tempfile.TemporaryDirectory() as d:
            module = Path(d) / 'renamed.so'; module.write_bytes(b'nchan_store_init_worker')
            original = m.read
            with patch.object(m, 'trusted', side_effect=lambda p: p), patch.object(m, 'read', side_effect=lambda p, *a: f'0000-1000 r-xp 0 00:00 1 {module}\n'.encode() if str(p).startswith('/proc/') else original(p, *a)):
                with self.assertRaisesRegex(ValueError, 'Nchan.*rollback'):
                    b.preflight()

    def test_original_ready_requires_exact_32_pid_starts_not_just_count(self):
        m = load(); b = m.Linux.__new__(m.Linux)
        b.old = [100, 10]; old = dict(identity=b.old)
        b.m = dict(old=old, workers=[[i, 10] for i in range(200, 232)])
        b.remaining = lambda: 1
        with patch.object(m, 'process', return_value=old), patch.object(m, 'children', return_value=[[i, 11] for i in range(200, 232)]):
            with self.assertRaisesRegex(ValueError, 'original workers changed'):
                b.original_ready()

    def test_pid_file_rejects_malformed_nonregular_and_unsafe_metadata(self):
        import stat
        from types import SimpleNamespace
        m = load()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'pid'; p.write_bytes(b'200\n'); p.chmod(0o644)
            fstat = m.os.fstat
            def owned(fd, **changes):
                s = fstat(fd)
                fields = {n: (0 if n in ('st_uid', 'st_gid') else getattr(s, n)) for n in dir(s) if n.startswith('st_')}
                fields.update(changes); return SimpleNamespace(**fields)
            with patch.object(m, 'trusted'), patch.object(m.os, 'fstat', side_effect=owned):
                for raw in (b'0\n', b'-1', b' 200', b'200\r\n', b'\xff', b'1'*33):
                    p.write_bytes(raw)
                    with self.subTest(raw=raw), self.assertRaises(ValueError): m.published_pid(p)
                p.write_bytes(b'')
                with self.assertRaises(m.PIDPublicationPending): m.published_pid(p)
                p.unlink()
                with self.assertRaises(m.PIDPublicationPending): m.published_pid(p)
                p.symlink_to(Path(d) / 'other')
                with self.assertRaises(OSError): m.published_pid(p)
                p.unlink(); os.mkfifo(p)
                with self.assertRaises(ValueError): m.published_pid(p)
                p.unlink(); p.write_bytes(b'200\n'); p.chmod(0o644)
            for changes in (dict(st_uid=1000), dict(st_gid=1000), dict(st_nlink=2), dict(st_mode=stat.S_IFREG | 0o666)):
                with patch.object(m, 'trusted'), patch.object(m.os, 'fstat', side_effect=lambda fd: owned(fd, **changes)):
                    with self.subTest(changes=changes), self.assertRaises(ValueError): m.published_pid(p)

    def test_identity_pair_must_be_coherent_and_same_lineage(self):
        m = load(); b = m.Linux.__new__(m.Linux)
        b.old = [100, 10]; b.s = dict(pid='/pid', binary='/nginx')
        b.m = dict(old=dict(cgroup='same'))
        good = dict(identity=[200, 20], parent=100, uid=0, title='nginx: master process /nginx', cgroup='same', exe='/nginx')
        for changes in (dict(parent=99), dict(cgroup='other'), dict(exe='/other'), dict(uid=1000), dict(identity=[200, 9])):
            with patch.object(m, 'published_pid', side_effect=[200, 100]), patch.object(m, 'process', return_value=dict(good, **changes)):
                with self.subTest(changes=changes), self.assertRaises(ValueError): b.publication_once()
        def proc(pid): return good if pid==200 else dict(identity=b.old)
        with patch.object(m, 'published_pid', side_effect=[200, 100, 201]), patch.object(m, 'process', side_effect=proc):
            with self.assertRaises(m.PIDPublicationPending): b.publication_once()

    def test_transient_retry_does_not_reset_deadline_or_retry_bad_identity(self):
        m = load(); b = m.Linux.__new__(m.Linux)
        for failure, expected in ((m.PIDPublicationPending('writer'), TimeoutError), (ValueError('lineage'), ValueError)):
            remaining = iter([.1, .1, 0])
            def left():
                value = next(remaining)
                if not value: raise TimeoutError('original budget')
                return value
            b.remaining = left
            with patch.object(b, 'publication_once', side_effect=failure) as once, patch.object(m.time, 'sleep'):
                with self.assertRaises(expected): b.discover()
                self.assertEqual(once.call_count, 1)


if __name__ == '__main__':
    unittest.main()
