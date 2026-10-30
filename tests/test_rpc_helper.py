"""Setup syscall contract without granting this test process privileges."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class FakeKernel:
    def __init__(self, denied=False, wrong_net=False, bad_drop=False):
        self.calls = []
        self.current = {'mnt': 1, 'net': 2, 'user': 3}
        self.denied, self.wrong_net, self.bad_drop = denied, wrong_net, bad_drop

    def namespaces(self):
        return dict(self.current)

    def unshare(self):
        self.calls.append('unshare')
        self.current['mnt'] = 4
        if self.wrong_net:
            self.current['net'] = 5

    def private(self):
        self.calls.append('private')

    def mount(self, target):
        self.calls.append('mount')
        if self.denied:
            raise PermissionError('denied')
        self.target = target

    def mountinfo(self):
        self.calls.append('mountinfo')
        return '10 1 0:10 / '+str(self.target)+' ro,nosuid,nodev,noexec - sysfs sysfs rw\n'

    def drop(self):
        self.calls.append('drop')
        if self.bad_drop:
            raise PermissionError('drop failed')

    def verify_caps(self):
        self.calls.append('verify_caps')


class HelperTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch('telemetry.rpc_helper.protect_memory')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_transient_endpoint_process_disables_piped_core_dumps(self):
        import subprocess
        import sys
        from telemetry import rpc
        self.assertTrue(hasattr(rpc, 'protect_memory'), 'piped core protection missing')
        result = subprocess.run([sys.executable, '-c',
                                 'import ctypes; from telemetry.rpc import protect_memory; '
                                 'protect_memory(); assert ctypes.CDLL(None).prctl(3,0,0,0,0) == 0'],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_units_separate_setup_privilege_from_general_collector(self):
        root = Path(__file__).resolve().parents[1]
        path = root/'ops/telemetry/keimirror-rpc-helper.service'
        self.assertTrue(path.is_file(), 'RPC setup service contract missing')
        unit = path.read_text()
        for directive in ('RuntimeDirectoryMode=0700', 'PrivateNetwork=no',
                          'RestrictAddressFamilies=AF_UNIX', 'LimitCORE=0', 'UMask=0077',
                          'CAP_SYS_ADMIN', 'CAP_SETPCAP', 'NoNewPrivileges=yes'):
            self.assertIn(directive, unit)
        collector = (root/'ops/telemetry/keimirror-status-collector.service').read_text()
        self.assertNotIn('CAP_SYS_ADMIN', collector)
        self.assertNotIn('rpc_helper.py', collector)
        self.assertIn('LimitCORE=0', collector, 'transient endpoints must not enter collector coredumps')

    def test_flat_installed_modules_match_existing_collector_layout(self):
        import shutil
        import subprocess
        import sys
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as name:
            source = Path(__file__).resolve().parents[1]/'telemetry'
            for filename in ('rates.py', 'rpc.py', 'rpc_helper.py'):
                shutil.copyfile(source/filename, Path(name)/filename)
            result = subprocess.run([sys.executable, '-E', '-s', '-c',
                                     'import rpc, rpc_helper; assert rpc.identity()[1].startswith("net:[")'],
                                    cwd=name, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_service_failure_is_sanitized_and_never_serves_before_setup(self):
        helper = self.module()
        self.assertTrue(hasattr(helper, 'main'), 'runnable helper service missing')
        with mock.patch.object(helper.os, 'geteuid', return_value=0), mock.patch.object(helper.os, 'umask'), mock.patch.object(helper, 'prepare', side_effect=PermissionError('private sensitive path')) as prepare, mock.patch.object(helper, 'serve') as serve:
            self.assertEqual(helper.main(), 1)
            prepare.assert_called_once()
            serve.assert_not_called()

    def test_caps_require_read_only_mask_empty_bounding_and_no_new_privs(self):
        helper = self.module()
        text = 'CapEff: 80004\nCapPrm: 80004\nCapInh: 0\nCapAmb: 0\nCapBnd: 0\nNoNewPrivs: 1\n'
        helper.verify_caps(text)
        for bad in (text.replace('80004', '280004'), text.replace('CapBnd: 0', 'CapBnd: 1'),
                    text.replace('NoNewPrivs: 1', 'NoNewPrivs: 0'), text.replace('CapAmb: 0', 'CapAmb: 1')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                helper.verify_caps(bad)

    def test_native_unshare_denied_without_host_mount_change(self):
        helper = self.module()
        if os.geteuid() == 0:
            self.skipTest('negative control is unprivileged only')
        before = Path('/proc/self/mountinfo').read_bytes()
        namespace = os.readlink('/proc/self/ns/mnt')
        with self.assertRaises(OSError):
            helper.Linux().unshare()
        self.assertEqual(os.readlink('/proc/self/ns/mnt'), namespace)
        self.assertEqual(Path('/proc/self/mountinfo').read_bytes(), before)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('telemetry.rpc_helper'), 'narrow privileged setup helper missing')
        from telemetry import rpc_helper
        return rpc_helper

    def test_setup_private_fresh_mount_then_drop_before_return(self):
        helper = self.module()
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as name:
            kernel = FakeKernel()
            proof = helper.prepare(Path(name)/'sysfs', kernel=kernel)
            self.assertEqual(kernel.calls, ['unshare', 'private', 'mount', 'mountinfo', 'drop', 'verify_caps'])
            self.assertEqual(proof, {'mnt': 4, 'net': 2, 'user': 3})

    def test_denied_mount_wrong_namespace_and_capdrop_never_return_reader(self):
        helper = self.module()
        for kernel in (FakeKernel(denied=True), FakeKernel(wrong_net=True), FakeKernel(bad_drop=True)):
            with self.subTest(kernel=kernel), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as name:
                with self.assertRaises((ValueError, PermissionError)):
                    helper.prepare(Path(name)/'sysfs', kernel=kernel)
                self.assertNotIn('verify_caps', kernel.calls)
                self.assertLessEqual(kernel.calls.count('mount'), 1)

    def test_mount_flags_are_per_mount_not_superblock(self):
        helper = self.module()
        good = '10 1 0:10 / /proof ro,nosuid,nodev,noexec - sysfs sysfs rw\n'
        helper.verify_mount('/proof', good)
        for bad in (good.replace('ro,nosuid', 'rw,nosuid'), good.replace('nodev,', ''),
                    good.replace(' - sysfs', ' shared:1 - sysfs'), good.replace(' /proof ', ' /else '),
                    good + good, good.replace(' / /proof ', ' /sub /proof ')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                helper.verify_mount('/proof', bad)
