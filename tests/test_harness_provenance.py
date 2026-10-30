"""Nonprivileged interception: never execute supplied artifacts."""
import importlib.util
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'ops/telemetry/http-reexec'
sys.path.insert(0, str(SOURCE))
import isolated as h
import root_stage as staging


class HarnessProvenance(unittest.TestCase):
    def test_direct_root_checkout_rejected_before_local_dependency_import(self):
        import builtins
        imported = []
        original = builtins.__import__
        def capture(name, *args, **kwargs):
            if name == 'root_stage': imported.append(name)
            return original(name, *args, **kwargs)
        spec = importlib.util.spec_from_file_location('untrusted_direct_harness', SOURCE / 'isolated.py')
        module = importlib.util.module_from_spec(spec)
        with patch.object(os, 'geteuid', return_value=0), patch.object(builtins, '__import__', side_effect=capture):
            with self.assertRaises(PermissionError):
                spec.loader.exec_module(module)
        self.assertEqual(imported, [])

    def test_bad_artifacts_fail_before_any_external_command(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            artifacts = base / 'artifacts'; artifacts.mkdir()
            for name in ('nginx', 'old.so', 'candidate.deb'):
                (artifacts / name).write_bytes(b'UNREVIEWED')
            (artifacts / 'reader').mkdir()
            for name in ('http_rates.py', 'private_reasons.py'):
                (artifacts / 'reader' / name).write_bytes(b'raise RuntimeError("UNREVIEWED")')
            def remap(value):
                p = Path(value)
                return base / p.name if str(p).startswith('/var/lib/keimirror-reexec-test-') else p
            calls = []
            def intercept(argv, **kwargs):
                calls.append(argv)
                raise ValueError('intercepted external execution')
            with patch.object(h, 'Path', side_effect=remap), patch.object(os, 'geteuid', return_value=0), patch.object(h.subprocess, 'run', side_effect=intercept):
                # Old code's source gate is not the artifact gate being tested.
                with patch.object(h.provenance, 'trusted'):
                    with self.assertRaises((ValueError, PermissionError)):
                        h.run(artifacts, base / 'out.json', 'forward')
            self.assertEqual(calls, [], 'unverified artifact reached external execution')

    def test_each_malformed_input_blocks_commands_and_transaction_code(self):
        for bad in staging.ARTIFACTS:
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as td:
                base = Path(td); source = base / 'inputs'; source.mkdir()
                (source / 'reader').mkdir()
                pins = {}
                for name in staging.ARTIFACTS:
                    data = ('approved fixture ' + name).encode()
                    (source / name).write_bytes(data)
                    pins[name] = staging.digest(data)
                (source / bad).write_bytes(b'UNREVIEWED')
                def remap(value):
                    p = Path(value)
                    return base / p.name if str(p).startswith('/var/lib/keimirror-reexec-test-') else p
                with patch.object(staging, 'ARTIFACTS', pins), patch.object(staging, 'trusted', side_effect=lambda p: p), patch.object(h, 'Path', side_effect=remap), patch.object(os, 'geteuid', return_value=0), patch.object(subprocess, 'run') as commands, patch('builtins.compile', side_effect=AssertionError('transaction code reached')) as compile_code:
                    with self.assertRaises(ValueError):
                        h.run(source, base / 'result.json', 'forward')
                    commands.assert_not_called()
                    compile_code.assert_not_called()

    def test_copy_captures_bytes_and_revalidates_actual_target(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source'; target = Path(td) / 'target'
            source.write_bytes(b'approved')
            original = staging.safe_read
            def race(path, *args):
                data = original(path, *args)
                if path == source:
                    source.write_bytes(b'changed-after-capture')
                return data
            with patch.object(staging, 'safe_read', side_effect=race):
                staging.copy_verified(source, target, staging.digest(b'approved'))
            self.assertEqual(target.read_bytes(), b'approved')
            self.assertNotEqual(source.stat().st_ino, target.stat().st_ino)
            target.unlink()
            source.write_bytes(b'approved')
            def corrupt_target(path, *args):
                if path == target:
                    target.chmod(0o600); target.write_bytes(b'raced-stage')
                return original(path, *args)
            with patch.object(staging, 'safe_read', side_effect=corrupt_target):
                with self.assertRaisesRegex(ValueError, 'staged-byte'):
                    staging.copy_verified(source, target, staging.digest(b'approved'))

    def test_links_fifo_oversize_and_ancestor_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); real = base / 'real'; real.mkdir()
            leaf = real / 'input'; leaf.write_bytes(b'approved')
            link = base / 'link'; link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(OSError): staging.safe_read(link / 'input')
            link.unlink(); link.symlink_to(leaf)
            with self.assertRaises(OSError): staging.safe_read(link)
            link.unlink(); os.link(leaf, link)
            with self.assertRaises(ValueError): staging.safe_read(leaf)
            link.unlink()
            with self.assertRaises(ValueError): staging.safe_read(leaf, limit=2)
            leaf.unlink(); os.mkfifo(leaf)
            with self.assertRaises(ValueError): staging.safe_read(leaf)

    def test_only_pinned_files_staged_no_cached_or_captured_libraries(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'source'; source.mkdir(); (source / 'reader').mkdir()
            pins = {}
            for name in staging.ARTIFACTS:
                data = name.encode(); (source / name).write_bytes(data)
                pins[name] = staging.digest(data)
            for name in ('reader/http_rates.pyc', 'reader/private_reasons.so', 'libc.so', 'manifest.json'):
                (source / name).write_bytes(b'UNREVIEWED')
            target = Path(td) / 'stage'
            with patch.object(staging, 'ARTIFACTS', pins):
                staging.stage_artifacts(source, target)
            self.assertEqual({str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()}, set(pins))

    def test_unapproved_manifest_rejected_before_any_copy(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)
            (source / 'root-staging-manifest.json').write_text('{"files": {}}')
            with self.assertRaisesRegex(ValueError, 'root-reviewed'):
                staging.manifest(source, '0' * 64)

    def test_committed_reader_pins_match_same_approved_sources(self):
        for name in ('http_rates.py', 'private_reasons.py'):
            self.assertEqual(staging.digest((SOURCE.parents[2] / 'telemetry' / name).read_bytes()), staging.ARTIFACTS['reader/' + name])

    def test_manifest_covers_all_root_python_and_transitive_local_imports(self):
        files = json.loads((SOURCE / 'root-staging-manifest.json').read_text())['files']
        self.assertEqual({p.name for p in SOURCE.glob('*.py')}, {n for n in files if n.endswith('.py') and '/' not in n})
        local = {Path(n).stem for n in files if n.endswith('.py')}
        for name, expected in files.items():
            path = SOURCE.parents[2] / 'telemetry' / Path(name).name if name.startswith('reader/') else SOURCE / name
            self.assertEqual(staging.digest(path.read_bytes()), expected, name)
            if name.endswith('.py'):
                for node in ast.walk(ast.parse(path.read_text())):
                    imports = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
                    for module in imports:
                        self.assertIn(module.split('.')[0], sys.stdlib_module_names | local, (name, module))

    def test_actual_source_stage_nonprivileged_with_mocked_owner_boundary(self):
        import shutil
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / 'source'; source.mkdir(); (source / 'reader').mkdir()
            manifest = json.loads((SOURCE / 'root-staging-manifest.json').read_text())
            for name in manifest['files']:
                path = SOURCE.parents[2] / 'telemetry' / Path(name).name if name.startswith('reader/') else SOURCE / name
                shutil.copyfile(path, source / name)
            shutil.copyfile(SOURCE / 'root-staging-manifest.json', source / 'root-staging-manifest.json')
            artifacts = base / 'artifacts'; artifacts.mkdir(); (artifacts / 'reader').mkdir()
            pins = {}
            for name in staging.ARTIFACTS:
                data = name.encode(); (artifacts / name).write_bytes(data); pins[name] = staging.digest(data)
            approved = staging.digest((source / 'root-staging-manifest.json').read_bytes())
            target = base / 'target'
            with patch.object(os, 'geteuid', return_value=0), patch.object(staging, 'trusted', side_effect=lambda p: p), patch.object(staging, 'ARTIFACTS', pins), patch.object(subprocess, 'run') as commands:
                staging.stage(source, artifacts, target, approved)
                staging.verify(target, approved)
                commands.assert_not_called()
                with self.assertRaises(FileExistsError): staging.stage(source, artifacts, target, approved)
                (target / 'inventory.py').chmod(0o600); (target / 'inventory.py').write_bytes(b'bad')
                with self.assertRaisesRegex(ValueError, 'source mismatch'): staging.verify(target, approved)


if __name__ == '__main__':
    unittest.main()
