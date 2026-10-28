"""Small synthetic fixtures only; real host inventories must remain private."""
import importlib
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BIN = "/opt/keimirror-telemetry/bin"


class TransformTests(unittest.TestCase):
    def adapter(self):
        self.assertTrue((ROOT / "telemetry/adapt.py").is_file(), "adapter is not implemented")
        return importlib.import_module("telemetry.adapt")

    def test_generic_replaces_only_the_binary_path_preserving_all_bytes(self):
        for binary in ("rsync", "rsync-ssl"):
            source = (f'#!/bin/bash\nRSYNC="/usr/bin/{binary}"\n'
                      '# opaque comment: \xff\n'
                      'flock -n 9 || exit 1\n'
                      '"$RSYNC" -a --delete --exclude="a b" "$UPSTREAM" "$DEST" 9>"$LOCK"\n')
            with self.subTest(binary=binary):
                expected = source.replace(f'/usr/bin/{binary}', f'{BIN}/{binary}')
                self.assertEqual(self.adapter().adapt_source("generic", source), expected)
                self.assertEqual(self.adapter().adapt_source("generic", source.encode("latin1")),
                                 expected.encode("latin1"))

    def test_generic_is_idempotent_and_rejects_ambiguous_or_unsupported_markers(self):
        adapt = self.adapter().adapt_source
        source = '#!/bin/sh\r\nRSYNC="/usr/bin/rsync"  \r\n"$RSYNC" "$@"\r\n'
        adapted = adapt("generic", source)
        self.assertEqual(adapt("generic", adapted), adapted)
        for bad in ('#!/bin/sh\n', 'RSYNC=rsync\n', 'RSYNC="/custom/rsync"\n',
                    source + source, source + 'RSYNC="/custom/rsync"\n',
                    source + adapted, source + 'export RSYNC=elsewhere\n',
                    source + 'true; RSYNC=elsewhere\n'):
            with self.subTest(source=bad):
                with self.assertRaises(ValueError):
                    adapt("generic", bad)
        with self.assertRaises(ValueError):
            adapt("unknown", source)

    def test_arch_replaces_both_command_tokens_without_changing_openssl_or_arguments(self):
        adapt = self.adapter().adapt_source
        source = ('#!/bin/bash\nif [ "$SSL" = yes ]; then\n'
                  '\t\tcmd=(rsync-ssl --type=openssl)\nelse\n\t\tcmd=(rsync)\nfi\n'
                  '"${cmd[@]}" -a "$@" "$DEST"\n')
        expected = source.replace('cmd=(rsync', f'cmd=({BIN}/rsync')
        self.assertEqual(adapt("arch", source), expected)
        self.assertEqual(adapt("arch", expected), expected)
        self.assertEqual(adapt("arch", source.encode()), expected.encode())
        for bad in (source + '\tcmd=(rsync)\n', source.replace(' --type=openssl', ''),
                    source.replace('cmd=(rsync)', 'cmd=(other)'),
                    source.replace('cmd=(rsync)', f'cmd=({BIN}/rsync)')):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                adapt("arch", bad)

    def test_opnsense_replaces_only_discovery_and_preserves_fallback(self):
        adapt = self.adapter().adapt_source
        marker = 'RSYNC_BIN="$(command -v rsync || true)"'
        fallback = '[[ -z "${RSYNC_BIN}" ]] && RSYNC_BIN="rsync"'
        source = f'#!/usr/bin/env bash\n{marker}\n{fallback}\n"$RSYNC_BIN" -a "$@"\n'
        expected = source.replace(marker, f'RSYNC_BIN="{BIN}/rsync"')
        self.assertEqual(adapt("opnsense", source), expected)
        self.assertEqual(adapt("opnsense", expected), expected)
        for bad in (source + marker + '\n', source.replace(marker, 'RSYNC_BIN="rsync"'),
                    source.replace(fallback, 'RSYNC_BIN="/other/rsync"'),
                    source.replace(fallback, ''), source + 'RSYNC_BIN="other"\n'):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                adapt("opnsense", bad)

    def test_kali_images_replaces_only_command_token_and_ftpsync_is_untouched(self):
        adapt = self.adapter().adapt_source
        command = '\trsync $RSYNC_OPTIONS $RSYNC_URL $TO >"$LOG_FILE" 2>"$ERROR_FILE"'
        source = '#!/bin/bash\nexec 9>"$LOCK"\nflock -n 9 || exit 0\n' + command + '\n'
        expected = source.replace('\trsync ', f'\t{BIN}/rsync ')
        self.assertEqual(adapt("kali-images", source), expected)
        self.assertEqual(adapt("kali-images", expected), expected)
        for bad in (source + command + '\n', source.replace('$TO', '"$TO"'),
                    source.replace('\trsync ', '\t/custom/rsync '), source + '\trsync "$@"\n'):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                adapt("kali-images", bad)
        ftpsync = b'#!/bin/bash\nRSYNC=${RSYNC:-rsync}\n$RSYNC --exclude=stage1 "$@"\n$RSYNC --delete "$@"\n'
        self.assertEqual(adapt("kali", ftpsync), ftpsync)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telemetry-adapt-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.webroot = self.root / "public"
        self.webroot.mkdir()
        self.config = self.root / "config.json"
        self.backups = self.root / "private-backups"
        self.repositories = {}
        self.originals = {}
        self.add_script("artix")

    def add_script(self, name, source=None, kind="generic"):
        path = self.root / (name + ".sh")
        if source is None:
            source = b'#!/bin/bash\nRSYNC="/usr/bin/rsync"\n"$RSYNC" -a "$@"\n'
        path.write_bytes(source)
        path.chmod(0o751)
        self.repositories[name] = {"script": str(path), "kind": kind,
                                   "path": str(self.webroot / name)}
        self.originals[path] = source
        self.config.write_text(json.dumps({"repositories": self.repositories}))
        return path

    def fixture_hashes(self):
        return {str(path): hashlib.sha256(source).hexdigest()
                for path, source in self.originals.items()}

    def fixture_manifest_args(self):
        # Reruns deliberately inventory the current (already adapted) fixtures.
        config = json.loads(self.config.read_text())
        hashes = {repo["script"]: hashlib.sha256(Path(repo["script"]).read_bytes()).hexdigest()
                  for repo in config["repositories"].values()}
        expected = self.root / "fixture-expected.json"
        expected.write_text(json.dumps(hashes))
        return ["--expected-hashes", str(expected)]

    def run_cli(self, *extra):
        return subprocess.run([sys.executable, str(ROOT / "telemetry/adapt.py"),
                               "--config", str(self.config), "--backup-dir", str(self.backups),
                               *extra], capture_output=True, text=True, timeout=15)

    def test_apply_without_manifest_rejects_cli_and_python_before_any_mutation(self):
        adapter = importlib.import_module("telemetry.adapt")
        for entrypoint in ("cli", "python"):
            with self.subTest(entrypoint=entrypoint):
                self.backups = self.root / ("must-not-create-" + entrypoint) / "backups"
                for path, source in self.originals.items():
                    path.write_bytes(source)
                before = {p: (p.read_bytes(), adapter._identity(p.stat())) for p in self.originals}
                if entrypoint == "cli":
                    result = self.run_cli("--apply")
                    rejected = result.returncode != 0
                else:
                    changes = adapter.prepare(json.loads(self.config.read_text()))
                    try:
                        adapter.apply_changes(changes, self.backups)
                    except ValueError:
                        rejected = True
                    else:
                        rejected = False
                self.assertEqual({p: (p.read_bytes(), adapter._identity(p.stat())) for p in before}, before)
                self.assertFalse(self.backups.parent.exists())
                self.assertTrue(rejected, "apply must require an expected-hashes manifest")

    def test_default_cli_prepares_hash_audit_without_writing_or_executing_sources(self):
        marker = self.root / "must-not-run"
        script = self.add_script("gentoo", b'#!/bin/sh\nRSYNC="/usr/bin/rsync"\n' +
                                 f'touch "{marker}"\n'.encode())
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip(), "dry-run must produce its preflight audit")
        report = json.loads(result.stdout)
        self.assertFalse(report["applied"])
        self.assertEqual(len(report["scripts"]), len(self.repositories))
        for record in report["scripts"]:
            path = Path(record["path"])
            self.assertEqual(record["before_sha256"], hashlib.sha256(self.originals[path]).hexdigest())
            self.assertTrue(record["changed"])
            self.assertNotIn("source", record)
        self.assertEqual(script.read_bytes(), self.originals[script])
        self.assertFalse(self.backups.exists())
        self.assertFalse(marker.exists())

    def test_apply_is_atomic_preserves_metadata_and_retains_original_private_backup_on_rerun(self):
        untouched = self.add_script("kali", b'#!/bin/bash\nRSYNC=${RSYNC:-rsync}\n', kind="kali")
        original_metadata = {p: p.stat() for p in self.originals}
        result = self.run_cli("--apply", *self.fixture_manifest_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["applied"])
        backup_content = {}
        for record in report["scripts"]:
            path = Path(record["path"])
            before = original_metadata[path]
            after = path.stat()
            self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                             (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)))
            self.assertEqual(record["readback_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            if path == untouched:
                self.assertFalse(record["changed"])
                self.assertEqual(after.st_ino, before.st_ino)
                continue
            self.assertNotEqual(after.st_ino, before.st_ino)
            self.assertIn(BIN.encode(), path.read_bytes())
            backup = Path(record["backup"])
            self.assertEqual(backup.read_bytes(), self.originals[path])
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(backup.stat().st_uid, os.geteuid())
            backup_content[backup] = (backup.read_bytes(), backup.stat().st_mtime_ns, backup.stat().st_ino)
        self.assertEqual(stat.S_IMODE(self.backups.stat().st_mode), 0o700)
        again = self.run_cli("--apply", *self.fixture_manifest_args())
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertFalse(any(r["changed"] for r in json.loads(again.stdout)["scripts"]))
        for path, contents in backup_content.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino), contents)

    def test_expected_hashes_guard_every_actual_baseline_before_any_mutation(self):
        self.add_script("gentoo")
        expected = self.root / "expected.json"
        hashes = {str(p): hashlib.sha256(data).hexdigest() for p, data in self.originals.items()}
        for bad in ({}, {**hashes, next(reversed(hashes)): "0" * 64},
                    {**hashes, "/not/in/config": "0" * 64},
                    {p: "not-a-hash" for p in hashes}):
            expected.write_text(json.dumps(bad))
            result = self.run_cli("--apply", "--expected-hashes", str(expected))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.backups.exists())
            for path, original in self.originals.items():
                self.assertEqual(path.read_bytes(), original)
        expected.write_text(json.dumps(hashes))
        result = self.run_cli("--apply", "--expected-hashes", str(expected))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backup_directory_must_be_private_outside_webroots_and_not_symlinked(self):
        insecure = self.root / "insecure"
        insecure.mkdir(mode=0o755)
        private = self.root / "private"
        private.mkdir(mode=0o700)
        linked = self.root / "linked"
        linked.symlink_to(private, target_is_directory=True)
        for directory in (self.webroot / "backups", insecure, linked):
            with self.subTest(directory=directory):
                for path, data in self.originals.items():
                    path.write_bytes(data)
                result = self.run_cli("--apply", "--backup-dir", str(directory), *self.fixture_manifest_args())
                self.assertNotEqual(result.returncode, 0, "unsafe backups must fail before any write")
                for path, original in self.originals.items():
                    self.assertEqual(path.read_bytes(), original)

    def test_preflight_rejects_symlink_hardlink_and_duplicate_script_targets(self):
        target = next(iter(self.originals))
        linked = self.root / "linked.sh"
        linked.symlink_to(target)
        for duplicate in (False, True):
            config = {"repositories": {**self.repositories,
                      "gentoo": {"script": str(target if duplicate else linked), "kind": "generic",
                                 "path": str(self.webroot / "gentoo")}}}
            self.config.write_text(json.dumps(config))
            with self.subTest(duplicate=duplicate):
                result = self.run_cli("--apply", *self.fixture_manifest_args())
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.backups.exists())
                self.assertEqual(target.read_bytes(), self.originals[target])
        linked.unlink()
        os.link(target, linked)
        self.config.write_text(json.dumps({"repositories": self.repositories}))
        result = self.run_cli("--apply", *self.fixture_manifest_args())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.backups.exists())

    def test_invalid_later_source_prevents_all_writes_and_does_not_leak_source(self):
        for source in (b'#!/bin/bash\nRSYNC="/usr/bin/rsync"\nif SECRET_PRIVATE_SYNTAX\n',
                       b'#!/bin/bash\nRSYNC="/other/rsync"\n',
                       b'#!/bin/python\nRSYNC="/usr/bin/rsync"\n'):
            self.add_script("gentoo", source)
            result = self.run_cli("--apply", *self.fixture_manifest_args())
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("SECRET_PRIVATE_SYNTAX", result.stderr + result.stdout)
            self.assertFalse(self.backups.exists())
            for path, original in self.originals.items():
                self.assertEqual(path.read_bytes(), original)

    def test_failure_after_a_replacement_rolls_back_all_attempted_targets(self):
        self.add_script("gentoo")
        adapter = importlib.import_module("telemetry.adapt")
        original_replace = adapter._atomic_replace
        changes = adapter.prepare(json.loads(self.config.read_text()))
        calls = []

        def fail_once_after_write(path, content, metadata):
            calls.append(path)
            original_replace(path, content, metadata)
            if len(calls) == 2:
                raise OSError("synthetic disk failure after rename")

        with patch.object(adapter, "_atomic_replace", side_effect=fail_once_after_write):
            with self.assertRaises((OSError, ValueError)):
                adapter.apply_changes(changes, self.backups, self.fixture_hashes())
        for path, original in self.originals.items():
            self.assertEqual(path.read_bytes(), original, "must rollback even a write that raised after rename")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o751)
        self.assertFalse(list(self.root.glob(".keimirror-adapt-*")))

    def test_changed_baseline_after_prepare_aborts_before_creating_backups(self):
        adapter = importlib.import_module("telemetry.adapt")
        path = self.add_script("gentoo")
        for mutation in ("content", "mode"):
            with self.subTest(mutation=mutation):
                path.write_bytes(self.originals[path])
                path.chmod(0o751)
                changes = adapter.prepare(json.loads(self.config.read_text()))
                if mutation == "content":
                    path.write_bytes(self.originals[path] + b'# external edit\n')
                else:
                    path.chmod(0o750)
                current = path.read_bytes()
                mode = stat.S_IMODE(path.stat().st_mode)
                with self.assertRaises(ValueError):
                    adapter.apply_changes(changes, self.backups, self.fixture_hashes())
                self.assertEqual(path.read_bytes(), current)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                self.assertFalse(self.backups.exists())

    def test_all_existing_backups_are_checked_before_any_new_backup_or_script_write(self):
        self.add_script("gentoo")
        adapter = importlib.import_module("telemetry.adapt")
        changes = adapter.prepare(json.loads(self.config.read_text()))
        self.backups.mkdir(mode=0o700)
        first = adapter.backup_path(self.backups, changes[0])
        second = adapter.backup_path(self.backups, changes[1])
        for problem in ("content", "permissions", "symlink"):
            with self.subTest(problem=problem):
                if second.exists() or second.is_symlink():
                    second.unlink()
                if problem == "symlink":
                    second.symlink_to(changes[1].path)
                else:
                    second.write_bytes(b"not the original" if problem == "content" else changes[1].before)
                    second.chmod(0o644 if problem == "permissions" else 0o600)
                result = self.run_cli("--apply", *self.fixture_manifest_args())
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(first.exists(), "all backups must be validated before creating even the first")
                for path, data in self.originals.items():
                    self.assertEqual(path.read_bytes(), data)

    def test_readback_verifies_content_and_metadata_then_rolls_back_corrupt_write(self):
        adapter = importlib.import_module("telemetry.adapt")
        original_replace = adapter._atomic_replace
        for corruption in ("content", "mode"):
            with self.subTest(corruption=corruption):
                for path, content in self.originals.items():
                    path.write_bytes(content)
                    path.chmod(0o751)
                changes = adapter.prepare(json.loads(self.config.read_text()))
                calls = []

                def corrupt_once(path, content, metadata):
                    original_replace(path, content, metadata)
                    calls.append(path)
                    if len(calls) == 1:
                        if corruption == "content":
                            path.write_bytes(content + b'# corrupt\n')
                        else:
                            path.chmod(0o700)

                with patch.object(adapter, "_atomic_replace", side_effect=corrupt_once):
                    with self.assertRaises(ValueError):
                        adapter.apply_changes(changes, self.backups, self.fixture_hashes())
                for path, original in self.originals.items():
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o751)

    def test_null_expected_hash_file_and_duplicate_json_keys_are_not_silent_opt_outs(self):
        expected = self.root / "expected.json"
        path = next(iter(self.originals))
        valid = hashlib.sha256(self.originals[path]).hexdigest()
        for data in ('null', '{' + json.dumps(str(path)) + ':"' + valid + '",' +
                     json.dumps(str(path)) + ':"' + valid + '"}'):
            with self.subTest(data=data):
                expected.write_text(data)
                result = self.run_cli("--apply", "--expected-hashes", str(expected))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), self.originals[path])
                self.assertFalse(self.backups.exists())

    def test_cli_identifies_incomplete_rollback_without_leaking_exception_contents(self):
        self.add_script("gentoo")
        adapter = importlib.import_module("telemetry.adapt")
        replace = adapter._atomic_replace
        calls = []

        def fail_after_first(path, content, metadata):
            calls.append(path)
            if len(calls) > 1:
                raise OSError("PRIVATE_PROVIDER_ERROR")
            replace(path, content, metadata)

        stderr = io.StringIO()
        with patch.object(adapter, "_atomic_replace", side_effect=fail_after_first), \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = adapter.main(["--config", str(self.config), "--backup-dir", str(self.backups), "--apply",
                                 *self.fixture_manifest_args()])
        self.assertNotEqual(code, 0)
        self.assertIn("rollback incomplete", stderr.getvalue())
        self.assertNotIn("PRIVATE_PROVIDER_ERROR", stderr.getvalue())
        self.assertEqual(len(calls), 4, "rollback must still attempt every prior target")

    def test_malformed_configuration_is_rejected_cleanly_before_mutation(self):
        manifest_args = self.fixture_manifest_args()
        for config in (None, [], {"repositories": []}, {"repositories": {}},
                       {"repositories": {"artix": None}},
                       {"repositories": {"artix": {**self.repositories["artix"], "path": "relative"}}}):
            with self.subTest(config=config):
                self.config.write_text(json.dumps(config))
                result = self.run_cli("--apply", *manifest_args)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.backups.exists())
                for path, source in self.originals.items():
                    self.assertEqual(path.read_bytes(), source)


if __name__ == "__main__":
    unittest.main()
