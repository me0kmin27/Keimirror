"""Size scans exercise only temporary fixture trees and injected executables."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


class SizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = self.root / "archlinux"
        self.tree.mkdir()
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "mirror_id": "mirror1", "public_host": "mirror.invalid",
            "projects": {"archlinux": ["archlinux"]},
            "repositories": {"archlinux": {"path": str(self.tree)}},
        }))
        self.state_dir = self.root / "state"
        self.cache = self.state_dir / "sizes.json"

    def scanner(self):
        self.assertTrue((REPO / "telemetry" / "sizes.py").is_file(),
                        "low-load size scanner has not been implemented")
        return importlib.import_module("telemetry.sizes")

    def executable(self, body, name="fake-du"):
        path = self.root / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)
        return path

    def test_scan_uses_low_priority_exact_du_arguments_and_resolved_root(self):
        sizes = self.scanner()
        alias = self.root / "alias"
        alias.symlink_to(self.tree, target_is_directory=True)
        config = json.loads(self.config.read_text())
        config["repositories"]["archlinux"]["path"] = str(alias)
        self.config.write_text(json.dumps(config))
        capture = self.root / "capture.json"
        program = self.executable(
            "import json, os, subprocess, sys\n"
            f"with open({str(capture)!r}, 'w') as stream:\n"
            "    json.dump({'args': sys.argv[1:], 'nice': os.getpriority(os.PRIO_PROCESS, 0),\n"
            "               'io': subprocess.check_output(['/usr/bin/ionice', '-p', str(os.getpid())], text=True)}, stream)\n"
            "print('4096\\t' + sys.argv[-1])\n"
        )
        self.assertEqual(sizes.scan(self.config, self.state_dir, program=program, now=lambda: 123.0), 0)
        captured = json.loads(capture.read_text())
        self.assertEqual(captured["args"], ["--summarize", "--block-size=1", "--one-file-system", "--", str(self.tree)])
        self.assertEqual(captured["nice"], 19)
        self.assertTrue(captured["io"].startswith("idle"))
        self.assertEqual(json.loads(self.cache.read_text()), {
            "schema_version": 1,
            "repositories": {"archlinux": {"bytes": 4096, "measured_at": 123.0, "error": False}},
        })
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o640)
    def test_failed_or_invalid_measurements_retain_the_original_timestamp(self):
        sizes = self.scanner()
        self.state_dir.mkdir()
        previous = {"schema_version": 1, "repositories": {
            "archlinux": {"bytes": 4096, "measured_at": 100.0, "error": False}}}
        cases = [
            ("print('8192\\t' + sys.argv[-1]); sys.exit(1)\n", True),
            ("print('not-a-size\\t' + sys.argv[-1])\n", True),
            ("print('-1\\t' + sys.argv[-1])\n", True),
            ("print('9007199254740992\\t' + sys.argv[-1])\n", True),
            ("print('1.5\\t' + sys.argv[-1])\n", True),
            ("print('12\\t' + sys.argv[-1]); print('extra record')\n", True),
            ("print('12\\t/wrong/root')\n", True),
            ("pass\n", True),
            ("print('0\\t' + sys.argv[-1])\n", False),
        ]
        for body, error in cases:
            with self.subTest(body=body):
                self.cache.write_text(json.dumps(previous))
                program = self.executable("import sys\n" + body)
                try:
                    result = sizes.scan(self.config, self.state_dir, program=program, now=lambda: 200)
                except Exception as exception:
                    result = type(exception).__name__
                self.assertEqual(result, 1 if error else 0)
                self.assertEqual(json.loads(self.cache.read_text())["repositories"]["archlinux"],
                                 {"bytes": 4096 if error else 0, "measured_at": 100.0 if error else 200,
                                  "error": error})
        self.cache.unlink()
        program = self.executable("raise SystemExit(1)\n")
        self.assertEqual(sizes.scan(self.config, self.state_dir, program=program, now=lambda: 300), 1)
        self.assertEqual(json.loads(self.cache.read_text())["repositories"]["archlinux"],
                         {"bytes": None, "measured_at": None, "error": True})
    def test_sweep_publishes_incrementally_and_rejects_overlapping_scans(self):
        self.scanner()
        second = self.root / "gentoo"
        second.mkdir()
        config = json.loads(self.config.read_text())
        config["repositories"]["gentoo"] = {"path": str(second)}
        self.config.write_text(json.dumps(config))
        ready = self.root / "second-repository-ready"
        release = self.root / "release"
        events = self.root / "events"
        program = self.executable(
            "import sys, time\nfrom pathlib import Path\n"
            "name = Path(sys.argv[-1]).name\n"
            f"with open({str(events)!r}, 'a') as stream: stream.write(name + '\\n')\n"
            "if name == 'gentoo':\n"
            f"    Path({str(ready)!r}).touch()\n"
            "    deadline = time.monotonic() + 4\n"
            f"    while not Path({str(release)!r}).exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
            "print(('4096' if name == 'archlinux' else '8192') + '\\t' + sys.argv[-1])\n"
        )
        driver = ("import sys; from telemetry.sizes import scan; "
                  "raise SystemExit(scan(sys.argv[1], sys.argv[2], program=sys.argv[3], timeout=5, now=lambda:100))")
        command = [sys.executable, "-c", driver, str(self.config), str(self.state_dir), str(program)]
        first = subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        overlap = None
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            incremental = json.loads(self.cache.read_text())
            self.assertEqual(incremental["repositories"], {
                "archlinux": {"bytes": 4096, "measured_at": 100, "error": False}})
            overlap = subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 1
            while overlap.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(overlap.poll(), 0, "an overlapping sweep must skip, not run or wait")
            self.assertEqual(events.read_text().splitlines(), ["archlinux", "gentoo"])
        finally:
            release.touch()
            first.communicate(timeout=6)
            if overlap is not None:
                overlap.communicate(timeout=6)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(json.loads(self.cache.read_text())["repositories"]["gentoo"],
                         {"bytes": 8192, "measured_at": 100, "error": False})
    def test_timeout_kills_measurement_descendants_and_continues_sweep(self):
        sizes = self.scanner()
        second = self.root / "gentoo"
        second.mkdir()
        config = json.loads(self.config.read_text())
        config["repositories"]["gentoo"] = {"path": str(second)}
        self.config.write_text(json.dumps(config))
        self.state_dir.mkdir()
        self.cache.write_text(json.dumps({"schema_version": 1, "repositories": {
            "archlinux": {"bytes": 4096, "measured_at": 100, "error": False}}}))
        pid_file = self.root / "pids.json"
        program = self.executable(
            "import json, os, subprocess, sys, time\nfrom pathlib import Path\n"
            "if Path(sys.argv[-1]).name == 'archlinux':\n"
            "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)'])\n"
            f"    Path({str(pid_file)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
            "    time.sleep(4)\n"
            "print('8192\\t' + sys.argv[-1])\n"
        )
        before = time.monotonic()
        pids = []
        try:
            self.assertEqual(sizes.scan(self.config, self.state_dir, program=program, timeout=0.3,
                                        now=lambda: 200), 1)
            self.assertLess(time.monotonic() - before, 2)
            pids = json.loads(pid_file.read_text())
            for pid in pids:
                stat = Path(f"/proc/{pid}/stat")
                self.assertTrue(not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z",
                                "timed-out measurements must leave no running descendants")
            records = json.loads(self.cache.read_text())["repositories"]
            self.assertEqual(records["archlinux"], {"bytes": 4096, "measured_at": 100, "error": True})
            self.assertEqual(records["gentoo"], {"bytes": 8192, "measured_at": 200, "error": False})
        finally:
            for pid in pids:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
    def test_corrupt_cache_never_supplies_unverified_old_bytes(self):
        sizes = self.scanner()
        self.state_dir.mkdir()
        program = self.executable("raise SystemExit(1)\n")
        invalid = [
            '{broken', '[]', '{}',
            json.dumps({"schema_version": True, "repositories": {"archlinux": {
                "bytes": 12, "measured_at": 100, "error": False}}}),
        ]
        for record in ({"bytes": -1, "measured_at": 100, "error": False},
                       {"bytes": True, "measured_at": 100, "error": False},
                       {"bytes": 9007199254740992, "measured_at": 100, "error": False},
                       {"bytes": 12, "measured_at": float("nan"), "error": False},
                       {"bytes": 12, "measured_at": None, "error": False},
                       {"bytes": 12, "measured_at": 100},
                       {"bytes": 12, "measured_at": 100, "error": "false"}):
            invalid.append(json.dumps({"schema_version": 1, "repositories": {"archlinux": record}}))
        for raw in invalid:
            with self.subTest(raw=raw):
                self.cache.write_text(raw)
                try:
                    result = sizes.scan(self.config, self.state_dir, program=program, now=lambda: 200)
                except Exception as exception:
                    result = type(exception).__name__
                self.assertEqual(result, 1)
                self.assertEqual(json.loads(self.cache.read_text()), {"schema_version": 1, "repositories": {
                    "archlinux": {"bytes": None, "measured_at": None, "error": True}}})
        self.cache.write_text(json.dumps({"schema_version": 1, "private_path": "/do/not/copy",
                                         "repositories": {
                                             "archlinux": {"bytes": 0, "measured_at": 100, "error": True,
                                                           "raw": "do not copy"},
                                             "unknown": {"bytes": 123, "measured_at": 100, "error": False}}}))
        self.assertEqual(sizes.scan(self.config, self.state_dir, program=program), 1)
        self.assertEqual(json.loads(self.cache.read_text()), {"schema_version": 1, "repositories": {
            "archlinux": {"bytes": 0, "measured_at": 100, "error": True}}})
    def test_bad_configuration_or_roots_never_start_a_measurement(self):
        sizes = self.scanner()
        marker = self.root / "executed"
        program = self.executable(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
            "import sys\nprint('12\\t' + sys.argv[-1])\n")
        file_root = self.root / "not-a-directory"
        file_root.touch()
        cases = ["{broken", "[]", "{}",
                 json.dumps({"repositories": []}),
                 json.dumps({"repositories": {"not-allowed": {"path": str(self.tree)}}}),
                 json.dumps({"repositories": {"archlinux": {"path": "relative/root"}}}),
                 json.dumps({"repositories": {"archlinux": {"path": str(file_root)}}}),
                 json.dumps({"repositories": {"archlinux": {"path": str(self.root / 'absent')}}}),
                 json.dumps({"repositories": {"archlinux": {"path": None}}})]
        for raw in cases:
            with self.subTest(raw=raw):
                marker.unlink(missing_ok=True)
                self.config.write_text(raw)
                try:
                    result = sizes.scan(self.config, self.state_dir, program=program)
                except Exception as exception:
                    result = type(exception).__name__
                self.assertEqual(result, 1)
                self.assertFalse(marker.exists())
        self.config.write_text(json.dumps({"repositories": {"archlinux": {"path": str(self.tree)}}}))
        for timeout in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout):
                marker.unlink(missing_ok=True)
                try:
                    result = sizes.scan(self.config, self.state_dir, program=program, timeout=timeout)
                except Exception as exception:
                    result = type(exception).__name__
                self.assertEqual(result, 1)
                self.assertFalse(marker.exists())
    def test_cli_real_du_counts_allocated_blocks_and_deduplicates_internal_hardlinks(self):
        self.scanner()
        data = self.tree / "data"
        data.write_bytes(b"x" * 16384)
        os.link(data, self.tree / "same-inode")
        sparse = self.tree / "sparse"
        with sparse.open("wb") as stream:
            stream.truncate(2 * 1024 * 1024)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "not-counted").write_bytes(b"x" * 65536)
        (self.tree / "internal-symlink").symlink_to(outside, target_is_directory=True)
        top = self.root / "trusted-top-symlink"
        top.symlink_to(self.tree, target_is_directory=True)
        self.config.write_text(json.dumps({"repositories": {"archlinux": {"path": str(top)}}}))
        unique = {(item.lstat().st_dev, item.lstat().st_ino): item.lstat().st_blocks * 512
                  for item in (self.tree, *self.tree.iterdir())}
        expected = sum(unique.values())
        before = time.time()
        result = subprocess.run([sys.executable, str(REPO / "telemetry" / "sizes.py"),
                                 "--config", str(self.config), "--state-dir", str(self.state_dir),
                                 "--timeout", "2"], capture_output=True, cwd=REPO, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.cache.exists(), "the sizes CLI must actually perform the fixture sweep")
        sample = json.loads(self.cache.read_text())["repositories"]["archlinux"]
        self.assertEqual(sample["bytes"], expected)
        self.assertFalse(sample["error"])
        self.assertGreaterEqual(sample["measured_at"], before)
        self.assertLessEqual(sample["measured_at"], time.time())
        help_result = subprocess.run([sys.executable, str(REPO / "telemetry" / "sizes.py"), "--help"],
                                     capture_output=True, text=True, timeout=5)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--timeout", help_result.stdout)
        self.assertIn("120", help_result.stdout)
    def test_nonregular_cache_or_config_fails_boundedly(self):
        self.scanner()
        program = self.executable("raise SystemExit(1)\n")
        fifo = self.root / "config-fifo"
        os.mkfifo(fifo)
        self.state_dir.mkdir()
        driver = ("import sys; from telemetry.sizes import scan; "
                  "raise SystemExit(scan(sys.argv[1], sys.argv[2], program=sys.argv[3]))")
        for which in ("config", "cache"):
            with self.subTest(which=which):
                config = fifo if which == "config" else self.config
                if which == "cache":
                    os.mkfifo(self.cache)
                child = subprocess.Popen([sys.executable, "-c", driver, str(config), str(self.state_dir), str(program)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=REPO)
                try:
                    try:
                        result = child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        result = None
                    self.assertEqual(result, 1)
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)
                    self.cache.unlink(missing_ok=True)
    def test_invalid_measurement_clock_keeps_old_sample_with_error(self):
        sizes = self.scanner()
        self.state_dir.mkdir()
        previous = {"schema_version": 1, "repositories": {
            "archlinux": {"bytes": 4096, "measured_at": 100, "error": False}}}
        program = self.executable("import sys\nprint('8192\\t' + sys.argv[-1])\n")
        for value in (-1, True, float("nan"), float("inf"), None):
            with self.subTest(value=value):
                self.cache.write_text(json.dumps(previous))
                self.assertEqual(sizes.scan(self.config, self.state_dir, program=program, now=lambda: value), 1)
                self.assertEqual(json.loads(self.cache.read_text())["repositories"]["archlinux"],
                                 {"bytes": 4096, "measured_at": 100, "error": True})


if __name__ == "__main__":
    unittest.main()
