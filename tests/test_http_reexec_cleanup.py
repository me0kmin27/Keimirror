"""Cleanup probes extract callback only; never run the privileged fixture."""
import ast
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch
from test_http_reexec import load

SOURCE = Path(__file__).with_name('http_reexec_nchan_runtime.py')


def callback(tx, owned=None):
    tree = ast.parse(SOURCE.read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    cleanup = next(n for n in run.body if isinstance(n, ast.FunctionDef) and n.name == 'cleanup_startup')
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'cleanup_fixture']
    ns = dict(tx=tx, root=Path('/exclusive-fixture'), owned=[] if owned is None else owned,
              time=time, os=Mock(), Path=Path)
    ns['os'].readlink.return_value = '/exclusive-fixture'
    exec(compile(ast.Module(body=helpers + [cleanup], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns['cleanup_startup'], ns


class CleanupTests(unittest.TestCase):
    def fake(self):
        tx = Mock()
        tx.PIDPublicationPending = load().PIDPublicationPending
        tx.process.return_value = dict(identity=[200, 20], exe='/exclusive-fixture/usr/sbin/nginx', uid=0, title='nginx: master process /nginx')
        return tx

    def test_first_read_failure_still_attempts_oldbin_and_reports_error(self):
        tx = self.fake()
        failure = ValueError('file changed or bound exceeded')
        tx.pidfile.side_effect = [failure, 200]
        tx.published_pid.side_effect = [failure, 200]
        proc = tx.process.return_value
        tx.process.side_effect = [proc, proc, ProcessLookupError()]
        fn, ns = callback(tx)
        with self.assertRaises(Exception) as caught: fn()
        self.assertIn('file changed or bound exceeded', str(caught.exception))
        reads = tx.published_pid.call_args_list or tx.pidfile.call_args_list
        self.assertEqual([str(c.args[0]) for c in reads], ['/exclusive-fixture/run/nginx.pid', '/exclusive-fixture/run/nginx.pid.oldbin'])
        tx.signal_identity.assert_called_once_with([200, 20], 'QUIT')

    def test_transient_first_read_retries_but_security_does_not(self):
        tx = self.fake()
        tx.published_pid.side_effect = [tx.PIDPublicationPending('writer'), 200, 200]
        proc = tx.process.return_value
        tx.process.side_effect = [proc, proc, proc, ProcessLookupError()]
        fn, ns = callback(tx)
        with patch.object(time, 'sleep'): fn()
        self.assertEqual(tx.published_pid.call_count, 3)
        tx.signal_identity.assert_called_once_with([200, 20], 'QUIT')

    def test_all_errors_aggregated_and_retained_identity_still_cleaned(self):
        tx = self.fake()
        tx.published_pid.side_effect = [PermissionError('primary denied'), ValueError('oldbin malformed')]
        proc = tx.process.return_value
        tx.process.side_effect = [proc, ProcessLookupError()]
        fn, ns = callback(tx, [[200, 20]])
        with self.assertRaisesRegex(RuntimeError, 'primary denied.*oldbin malformed'): fn()
        tx.signal_identity.assert_called_once_with([200, 20], 'QUIT')

    def test_foreign_exe_root_or_reused_start_never_signalled(self):
        for field, value in (('exe', '/foreign'), ('root', '/foreign'), ('identity', [200, 99]), ('uid', 1000), ('title', 'other')):
            tx = self.fake()
            tx.published_pid.side_effect = FileNotFoundError()
            if field != 'root': tx.process.return_value[field] = value
            fn, ns = callback(tx, [[200, 20]])
            if field == 'root': ns['os'].readlink.return_value = value
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'unproved'): fn()
            tx.signal_identity.assert_not_called()

    def test_alive_or_unreadable_after_quit_is_not_success(self):
        for failure in (None, PermissionError('absence denied')):
            tx = self.fake(); tx.published_pid.side_effect = FileNotFoundError()
            if failure: tx.process.side_effect = [tx.process.return_value, failure]
            fn, ns = callback(tx, [[200, 20]])
            with patch.object(time, 'monotonic', side_effect=iter(range(0, 100, 10))), self.assertRaisesRegex(RuntimeError, 'absence'): fn()
            tx.signal_identity.assert_called_once_with([200, 20], 'QUIT')

    def test_missing_chroot_evidence_does_not_mean_process_absent(self):
        tx = self.fake(); tx.published_pid.side_effect = FileNotFoundError()
        fn, ns = callback(tx, [[200, 20]])
        ns['os'].readlink.side_effect = FileNotFoundError('root evidence missing')
        with self.assertRaisesRegex(RuntimeError, 'root evidence missing'): fn()
        tx.signal_identity.assert_not_called()

    def test_pidfile_cannot_reauthorize_a_reused_retained_pid(self):
        tx = self.fake()
        tx.published_pid.side_effect = [200, FileNotFoundError()]
        tx.process.return_value['identity'] = [200, 99]
        fn, ns = callback(tx, [[200, 20]])
        with self.assertRaisesRegex(RuntimeError, 'reused|unproved'): fn()
        tx.signal_identity.assert_not_called()

    def test_publication_churn_deadline_still_attempts_second_target(self):
        tx = self.fake()
        tx.published_pid.side_effect = [tx.PIDPublicationPending('writer'), 200]
        proc = tx.process.return_value
        tx.process.side_effect = [proc, proc, ProcessLookupError()]
        fn, ns = callback(tx)
        with patch.object(time, 'monotonic', side_effect=iter(range(0, 100, 10))), self.assertRaisesRegex(RuntimeError, 'publication deadline'): fn()
        self.assertEqual(tx.published_pid.call_count, 2)
        tx.signal_identity.assert_called_once_with([200, 20], 'QUIT')

    def test_harness_wait_retries_structural_churn_not_valueerror_text(self):
        tx = load()
        tree = ast.parse(SOURCE.read_text())
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        wait = next(n for n in run.body if isinstance(n, ast.FunctionDef) and n.name == 'wait')
        ns = dict(tx=tx, time=time, deadline=time.monotonic() + 5)
        exec(compile(ast.Module(body=[wait], type_ignores=[]), str(SOURCE), 'exec'), ns)
        for error, retries in ((tx.ProcessSnapshotChurn('race'), True), (ValueError('child inventory changed'), False)):
            fn = Mock(side_effect=[error, True])
            with patch.object(time, 'sleep'):
                if retries: self.assertTrue(ns['wait'](fn))
                else:
                    with self.assertRaises(ValueError): ns['wait'](fn)
            self.assertEqual(fn.call_count, 2 if retries else 1)

    def test_runtime_retains_both_masters_and_finally_uses_verified_cleanup(self):
        tree = ast.parse(SOURCE.read_text())
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        appends = [ast.unparse(n) for n in ast.walk(run) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'owned.append']
        self.assertEqual(appends, ['owned.append(old)', 'owned.append(new)'])
        final = next(n.finalbody for n in run.body if isinstance(n, ast.Try))
        text = '\n'.join(ast.unparse(n) for n in final)
        self.assertIn('cleanup_startup()', text)
        self.assertIn('atexit.unregister(cleanup_startup)', text)
        self.assertNotIn('signal_identity', text)
        test_block = next(n for n in run.body if isinstance(n, ast.Try))
        self.assertNotIn("'result.json'", ast.unparse(test_block))
        self.assertIn("'result.json'", ast.unparse(run.body[-1]))


if __name__ == '__main__':
    unittest.main()
