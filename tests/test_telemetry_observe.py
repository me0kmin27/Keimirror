"""Isolated subprocess fixtures; never invoke a real mirror transfer."""
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


class ObserveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "mirror" / "archlinux"
        self.destination.mkdir(parents=True)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "mirror_id": "mirror1", "public_host": "mirror.invalid",
            "projects": {"archlinux": ["archlinux"]},
            "repositories": {"archlinux": {"path": str(self.destination)}},
        }))
        self.state_dir = self.root / "state"
        self.state_file = self.state_dir / "runs" / "archlinux" / "state.json"

    def observer(self):
        self.assertTrue((REPO / "telemetry" / "observe.py").is_file(),
                        "transparent observation module has not been implemented")
        return importlib.import_module("telemetry.observe")

    def executable(self, body, name="fake-rsync"):
        path = self.root / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)
        return path

    def test_success_records_live_identity_and_terminal_state(self):
        observe = self.observer()
        running_copy = self.root / "running.json"
        program = self.executable(
            "from pathlib import Path\n"
            f"Path({str(running_copy)!r}).write_bytes(Path({str(self.state_file)!r}).read_bytes())\n"
        )
        import inspect
        self.assertIn("now", inspect.signature(observe.run).parameters,
                      "observer needs an injectable clock for recorded events")
        times = iter([100.0, 110.0])
        result = observe.run(self.config, self.state_dir,
                             ["source.invalid::repo", str(self.destination)],
                             program=program, now=lambda: next(times))
        self.assertEqual(result, 0)
        running = json.loads(running_copy.read_text())
        self.assertEqual(set(running), {
            "schema_version", "repository", "phase", "pid", "start_ticks", "boot_id",
            "started_at", "finished_at", "exit_code", "last_success_at",
        })
        self.assertEqual(running["schema_version"], 1)
        self.assertEqual(running["repository"], "archlinux")
        self.assertEqual(running["phase"], "running")
        self.assertEqual(running["pid"], os.getpid())
        stat_fields = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
        self.assertEqual(running["start_ticks"], int(stat_fields[19]))
        self.assertEqual(running["boot_id"], Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        self.assertEqual(running["started_at"], 100.0)
        self.assertIsNone(running["finished_at"])
        self.assertIsNone(running["exit_code"])
        self.assertIsNone(running["last_success_at"])
        finished = json.loads(self.state_file.read_text())
        self.assertEqual(finished, dict(running, phase="finished", finished_at=110.0,
                                        exit_code=0, last_success_at=110.0))

    def test_only_final_absolute_destination_selects_allowed_repository(self):
        observe = self.observer()
        program = self.executable("pass\n")
        alias = self.root / "alias"
        alias.symlink_to(self.destination, target_is_directory=True)
        outside = self.root / "outside"
        outside.mkdir()
        (self.destination / "escape").symlink_to(outside, target_is_directory=True)
        config = json.loads(self.config.read_text())
        config["repositories"]["archlinux"]["path"] = str(alias)
        config["repositories"]["kali"] = {"path": str(self.destination / "kali")}
        self.config.write_text(json.dumps(config))
        cases = [
            (["src", str(alias)], "archlinux"),
            (["src", str(alias / "lastsync")], "archlinux"),
            (["src", str(self.destination / "subdir")], "archlinux"),
            (["src", str(self.destination / "kali" / "pool")], "kali"),
            ([str(self.destination), "upstream.invalid::remote"], None),
            (["src", str(self.destination) + "-unrelated"], None),
            (["src", "relative/mirror/archlinux"], None),
            (["src", "--log-file=" + str(self.destination)], None),
            (["src", str(alias / ".." / "other")], None),
            (["src", str(self.destination / "escape" / "file")], None),
            (["--version"], None),
            ([], None),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                for path in self.state_dir.glob("runs/*/state.json"):
                    path.unlink()
                result = observe.run(self.config, self.state_dir, arguments, program=program)
                self.assertEqual(result, 0)
                recorded = list(self.state_dir.glob("runs/*/state.json"))
                self.assertEqual([path.parent.name for path in recorded],
                                 [expected] if expected else [])
        config["repositories"] = {"../../not-allowed": {"path": str(alias)}}
        self.config.write_text(json.dumps(config))
        self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(alias)], program=program), 0)
        self.assertFalse((self.state_dir / "not-allowed" / "state.json").exists())

    def test_observation_failures_never_block_or_change_transfer_result(self):
        observe = self.observer()
        program = self.executable("raise SystemExit(23)\n")
        corrupt = self.root / "corrupt.json"
        corrupt.write_text("{broken")
        wrong_shape = self.root / "wrong.json"
        wrong_shape.write_text("[]")
        blocked = self.root / "not-a-directory"
        blocked.write_text("cannot create state here")

        def broken_clock():
            raise OSError("clock fixture failed")

        cases = [
            (self.root / "missing-config", self.state_dir, lambda: 100),
            (corrupt, self.state_dir, lambda: 100),
            (wrong_shape, self.state_dir, lambda: 100),
            (self.config, blocked, lambda: 100),
            (self.config, self.state_dir, broken_clock),
        ]
        for config, state, clock in cases:
            with self.subTest(config=config, state=state):
                try:
                    result = observe.run(config, state, ["src", str(self.destination)],
                                         program=program, now=clock)
                except Exception as error:
                    result = type(error).__name__
                self.assertEqual(result, 23, "telemetry failure must not stop the original binary")

    def test_failure_retains_only_a_validated_prior_success(self):
        observe = self.observer()
        success = self.executable("pass\n", "success")
        times = iter([100.0, 110.0])
        observe.run(self.config, self.state_dir, ["src", str(self.destination)],
                    program=success, now=lambda: next(times))
        valid = json.loads(self.state_file.read_text())
        failure = self.executable("raise SystemExit(24)\n", "failure")
        cases = [
            (valid, 110.0),
            (dict(valid, phase="running", started_at=120, finished_at=None, exit_code=None), 110.0),
            (dict(valid, phase="finished", started_at=120, finished_at=130, exit_code=23), 110.0),
            ({"last_success_at": 110.0}, None),
            (dict(valid, schema_version=True), None),
            (dict(valid, repository="kali"), None),
            (dict(valid, phase="completed"), None),
            (dict(valid, pid=True), None),
            (dict(valid, start_ticks=-1), None),
            (dict(valid, boot_id="not-a-boot-id"), None),
            (dict(valid, last_success_at=float("nan")), None),
            (dict(valid, started_at=300, finished_at=310, last_success_at=310), None),
            (dict(valid, last_success_at=109), None),
            (dict(valid, exit_code=False), None),
            (dict(valid, exit_code=999), None),
            (dict(valid, finished_at=99), None),
            ([], None),
        ]
        for previous, expected in cases:
            with self.subTest(previous=previous):
                self.state_file.write_text(json.dumps(previous))
                times = iter([200.0, 210.0])
                self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)],
                                            program=failure, now=lambda: next(times)), 24)
                finished = json.loads(self.state_file.read_text())
                self.assertEqual(finished["exit_code"], 24)
                self.assertEqual(finished["last_success_at"], expected)
        self.state_file.write_text("{broken")
        self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)],
                                    program=failure), 24)
        self.assertIsNone(json.loads(self.state_file.read_text())["last_success_at"])

    def test_state_replacement_is_atomic_private_and_symlink_safe(self):
        observe = self.observer()
        program = self.executable("pass\n")
        old_mask = os.umask(0o077)
        try:
            self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)],
                                        program=program), 0)
        finally:
            os.umask(old_mask)
        self.assertEqual(self.state_file.stat().st_mode & 0o777, 0o640)
        for directory in (self.state_dir, self.state_dir / "runs", self.state_file.parent):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
        before = self.state_file.read_bytes()
        with self.state_file.open("rb") as old_reader:
            self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)],
                                        program=program), 0)
            self.assertEqual(old_reader.read(), before, "an existing reader must see the old complete inode")
        sentinel = self.root / "do-not-overwrite"
        sentinel.write_text('{"private": "untouched"}')
        self.state_file.unlink()
        self.state_file.symlink_to(sentinel)
        self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)],
                                    program=program), 0)
        self.assertEqual(sentinel.read_text(), '{"private": "untouched"}')
        self.assertFalse(self.state_file.is_symlink())
        self.assertEqual(list(self.state_file.parent.glob("*.tmp")), [])

    def start_wrapper(self, program, arguments):
        driver = ("import resource, sys; from telemetry import observe; "
                  "resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); "
                  "result = observe.run(sys.argv[1], sys.argv[2], sys.argv[4:], program=sys.argv[3]); "
                  "getattr(observe, 'exit_with_status', sys.exit)(result)")
        child = subprocess.Popen(
            [sys.executable, "-c", driver, str(self.config), str(self.state_dir), str(program), *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=REPO,
        )

        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=6)

        self.addCleanup(cleanup)
        return child

    def wait_file(self, path):
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), f"fixture did not become ready: {path}")

    def test_older_completion_cannot_overwrite_newer_run(self):
        self.observer()
        program = self.executable(
            "import sys, time\nfrom pathlib import Path\n"
            f"root = Path({str(self.root)!r})\n"
            "name = sys.argv[1]\n"
            "(root / (name + '.ready')).touch()\n"
            "deadline = time.monotonic() + 5\n"
            "while not (root / (name + '.release')).exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.01)\n"
            "sys.exit(int(sys.argv[2]))\n"
        )
        older = self.start_wrapper(program, ["old", "0", str(self.destination)])
        self.wait_file(self.root / "old.ready")
        newer = self.start_wrapper(program, ["new", "23", str(self.destination)])
        self.wait_file(self.root / "new.ready")
        self.assertIsNone(older.poll(), "state synchronization must not serialize transfers")
        running = json.loads(self.state_file.read_text())
        self.assertEqual(running["pid"], newer.pid)
        (self.root / "new.release").touch()
        self.assertEqual(newer.wait(timeout=5), 23)
        new_finished = self.state_file.read_bytes()
        (self.root / "old.release").touch()
        self.assertEqual(older.wait(timeout=5), 0)
        self.assertEqual(self.state_file.read_bytes(), new_finished)
        self.assertEqual(json.loads(new_finished)["exit_code"], 23)

    def test_missing_native_program_returns_127_with_failed_observation(self):
        observe = self.observer()
        try:
            result = observe.run(self.config, self.state_dir, ["src", str(self.destination)],
                                 program=self.root / "missing-native-rsync")
        except OSError as error:
            result = type(error).__name__
        self.assertEqual(result, 127)
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["phase"], "finished")
        self.assertEqual(state["exit_code"], 127)
        self.assertIsNone(state["last_success_at"])

    def test_signals_reach_native_process_and_descendants_without_orphans(self):
        self.observer()
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1,
                       signal.SIGUSR2, signal.SIGALRM, signal.SIGPIPE, signal.SIGQUIT):
            with self.subTest(signum=signum):
                ready = self.root / f"ready-{signum}"
                marker = self.root / f"grandchild-signal-{signum}"
                grand_ready = self.root / f"grandchild-ready-{signum}"
                grand = self.executable(
                    "import os, signal, time\nfrom pathlib import Path\n"
                    "def stop(number, frame):\n"
                    f"    Path({str(marker)!r}).write_text(str(number))\n"
                    "    signal.signal(number, signal.SIG_DFL)\n"
                    "    os.kill(os.getpid(), number)\n"
                    "for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1, signal.SIGUSR2, signal.SIGALRM, signal.SIGPIPE, signal.SIGQUIT):\n"
                    "    signal.signal(number, stop)\n"
                    f"Path({str(grand_ready)!r}).touch()\n"
                    "time.sleep(5)\n", f"grand-{signum}"
                )
                program = self.executable(
                    "import json, os, signal, subprocess, time\nfrom pathlib import Path\n"
                    f"child = subprocess.Popen([{str(grand)!r}])\n"
                    "def stop(number, frame):\n"
                    "    child.wait(timeout=2)\n"
                    "    signal.signal(number, signal.SIG_DFL)\n"
                    "    os.kill(os.getpid(), number)\n"
                    "for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1, signal.SIGUSR2, signal.SIGALRM, signal.SIGPIPE, signal.SIGQUIT):\n"
                    "    signal.signal(number, stop)\n"
                    f"while not Path({str(grand_ready)!r}).exists(): time.sleep(0.01)\n"
                    f"Path({str(ready)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
                    "child.wait(timeout=5)\n", f"native-{signum}"
                )
                wrapper = self.start_wrapper(program, ["src", str(self.destination)])
                self.wait_file(ready)
                pids = json.loads(ready.read_text())
                try:
                    wrapper.send_signal(signum)
                    self.assertEqual(wrapper.wait(timeout=4), -signum)
                    state = json.loads(self.state_file.read_text())
                    self.assertEqual(state["phase"], "finished")
                    self.assertEqual(state["exit_code"], 128 + signum)
                    self.assertIsNone(state["last_success_at"])
                    self.assertEqual(marker.read_text(), str(signum))
                    for pid in pids:
                        self.assertFalse(Path(f"/proc/{pid}").exists(), "wrapper must reap its native process")
                finally:
                    for pid in reversed(pids):
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass

    def test_job_group_sigkill_leaves_no_live_native_tree_or_lock(self):
        import fcntl
        ready = self.root / "tree.ready"
        lock_path = self.root / "transfer.lock"
        program = self.executable(
            "import fcntl, json, os, subprocess, time\nfrom pathlib import Path\n"
            f"lock = open({str(lock_path)!r}, 'w')\n"
            "fcntl.flock(lock, fcntl.LOCK_EX)\n"
            "os.set_inheritable(lock.fileno(), True)\n"
            "child = subprocess.Popen(['/bin/sleep', '30'], close_fds=False)\n"
            f"Path({str(ready)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
            "child.wait()\n"
        )
        driver = "import sys; from telemetry.observe import run; sys.exit(run(sys.argv[1], sys.argv[2], [], program=sys.argv[3]))"
        wrapper = subprocess.Popen([sys.executable, '-c', driver, str(self.config),
                                    str(self.state_dir), str(program)], cwd=REPO,
                                   start_new_session=True)
        pids = []
        try:
            self.wait_file(ready)
            pids = json.loads(ready.read_text())
            os.killpg(wrapper.pid, signal.SIGKILL)
            self.assertEqual(wrapper.wait(timeout=3), -signal.SIGKILL)
            deadline = time.monotonic() + 2
            def live(pid):
                try:
                    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
                except (FileNotFoundError, ProcessLookupError):
                    return False
            while any(live(pid) for pid in pids) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(any(live(pid) for pid in pids), 'job group kill orphaned native transfer')
            with lock_path.open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            for pid in reversed(pids):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if wrapper.poll() is None:
                wrapper.kill()
            wrapper.wait(timeout=3)

    def installed_shim(self, name, program):
        """Exercise shipped shims with only installation paths relocated."""
        import shlex
        observer = self.root / 'installed-observe.py'
        source = (REPO / 'telemetry/observe.py').read_text()
        source = source.replace('choices=("/usr/bin/rsync", "/usr/bin/rsync-ssl")',
                                f'choices=({str(program)!r},)')
        observer.write_text(source)
        source = (REPO / 'ops/telemetry/bin' / name).read_text()
        for old, new in (('/usr/bin/python3', sys.executable),
                         ('/opt/keimirror-telemetry/observe.py', observer),
                         ('/etc/keimirror-telemetry/config.json', self.config),
                         ('/var/lib/keimirror-telemetry', self.state_dir),
                         (f'/usr/bin/{name}', program)):
            source = source.replace(old, shlex.quote(str(new)))
        shim = self.root / name
        shim.write_text(source)
        shim.chmod(0o755)
        return shim

    def test_installed_shims_reap_failure_with_inherited_sigchld_ignored(self):
        # Set dispositions in an execing launcher, not unsafe preexec_fn test code.
        launcher = ('import os, signal, sys; '
                    'signal.signal(signal.SIGCHLD, signal.SIG_IGN); '
                    'os.execv(sys.argv[1], sys.argv[1:])')
        for name in ('rsync', 'rsync-ssl'):
            with self.subTest(name=name):
                shim = self.installed_shim(name, '/usr/bin/false')
                result = subprocess.run([sys.executable, '-c', launcher, str(shim),
                                         'src', str(self.destination)], capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 1, result.stderr)
                state = json.loads(self.state_file.read_text())
                self.assertEqual(state['phase'], 'finished')
                self.assertEqual(state['exit_code'], 1)
                self.assertIsNone(state['last_success_at'])

    def native_signal_fixture(self):
        """A non-interpreter native probe: Python would hide SIGPIPE/XFSZ."""
        source = self.root / 'signal-probe.c'
        source.write_text(r'''
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
static pid_t descendant;
static void stop(int n) {
    if (descendant == 0) {
        char byte = (char)n;
        write(1, &byte, 1);
        _exit(0);
    }
    waitpid(descendant, NULL, 0);
    signal(n, SIG_DFL);
    kill(getpid(), n);
}
int main(int argc, char **argv) {
    if (argv[1][0] == 'i') {
        unsigned long long ignored = 0;
        struct sigaction action;
        for (int n = 1; n < NSIG; n++)
            if (sigaction(n, NULL, &action) == 0 && action.sa_handler == SIG_IGN)
                ignored |= 1ULL << (n - 1);
        char path[80];
        snprintf(path, sizeof(path), "/proc/%d/task", getppid());
        DIR *tasks = opendir(path);
        struct dirent *entry;
        int count = 0;
        while ((entry = readdir(tasks))) if (entry->d_name[0] != '.') count++;
        closedir(tasks);
        printf("%llx %d %d\n", ignored, getpgrp(), count);
        return 23;
    }
    if (argv[1][0] == 'w') {
        FILE *file = fopen(argv[2], "w");
        fprintf(file, "%d", getpid());
        fclose(file);
        while (access(argv[3], F_OK) != 0) usleep(10000);
        return 23;
    }
    int n = atoi(argv[1]), ready[2];
    pipe(ready);
    signal(n, stop);
    descendant = fork();
    if (descendant == 0) {
        close(ready[0]);
        write(ready[1], "r", 1);
        close(ready[1]);
        for (;;) pause();
    }
    close(ready[1]);
    char byte;
    read(ready[0], &byte, 1);
    close(ready[0]);
    FILE *file = fopen(argv[2], "w");
    fprintf(file, "[%d,%d,%d]", getpid(), descendant, getpgrp());
    fclose(file);
    for (;;) pause();
}
''')
        binary = self.root / 'signal-probe'
        subprocess.run(['gcc', '-Wall', '-Werror', '-o', str(binary), str(source)],
                       capture_output=True, check=True, timeout=10)
        return binary

    def test_installed_shims_preserve_native_signal_ignores_and_single_threaded_parent(self):
        program = self.native_signal_fixture()
        launcher = ('import os, signal, sys; '
                    '[signal.signal(int(n), signal.SIG_IGN) for n in sys.argv[1].split(",")]; '
                    'os.execv(sys.argv[2], sys.argv[2:])')
        ignored = ','.join(str(int(n)) for n in
                           (signal.SIGCHLD, signal.SIGXCPU, signal.SIGXFSZ, signal.SIGPROF,
                            signal.SIGVTALRM, signal.SIGIO, signal.SIGPWR, signal.SIGHUP))
        direct = subprocess.run([sys.executable, '-c', launcher, ignored, str(program), 'inspect'],
                                capture_output=True, timeout=5)
        self.assertEqual(direct.returncode, 23)
        for name in ('rsync', 'rsync-ssl'):
            with self.subTest(name=name):
                shim = self.installed_shim(name, program)
                result = subprocess.run([sys.executable, '-c', launcher, ignored, str(shim),
                                         'inspect', 'src', str(self.destination)],
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 23, result.stderr)
                self.assertEqual(result.stdout, direct.stdout)
                self.assertEqual(result.stdout.split()[-1], b'1', 'observer must be single threaded')
                self.assertEqual(json.loads(self.state_file.read_text())['exit_code'], 23)

    def test_installed_shims_keep_inherited_termination_ignores_live(self):
        program = self.native_signal_fixture()
        numbers = (signal.SIGXCPU, signal.SIGXFSZ, signal.SIGPROF, signal.SIGVTALRM,
                   signal.SIGIO, signal.SIGPWR, signal.SIGHUP)
        launcher = ('import os, signal, sys; '
                    '[signal.signal(int(n), signal.SIG_IGN) for n in sys.argv[1].split(",")]; '
                    'os.execv(sys.argv[2], sys.argv[2:])')
        ignored = ','.join(str(int(n)) for n in (*numbers, signal.SIGCHLD))
        for name in ('rsync', 'rsync-ssl'):
            with self.subTest(name=name):
                shim = self.installed_shim(name, program)
                ready, release = self.root / f'{name}.ready', self.root / f'{name}.release'
                wrapper = subprocess.Popen([sys.executable, '-c', launcher, ignored, str(shim),
                                            'wait', str(ready), str(release), 'src', str(self.destination)],
                                           start_new_session=True, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE)
                try:
                    self.wait_file(ready)
                    native = int(ready.read_text())
                    for number in numbers:
                        os.kill(wrapper.pid, number)
                        os.kill(native, number)
                    time.sleep(.1)
                    self.assertIsNone(wrapper.poll())
                    self.assertTrue(Path(f'/proc/{native}').exists())
                    self.assertEqual(json.loads(self.state_file.read_text())['phase'], 'running')
                    release.touch()
                    self.assertEqual(wrapper.wait(timeout=3), 23)
                    self.assertEqual(json.loads(self.state_file.read_text())['exit_code'], 23)
                finally:
                    try:
                        os.killpg(wrapper.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    wrapper.communicate(timeout=3)

    def test_installed_shims_forward_all_default_termination_signals_to_native_tree(self):
        program = self.native_signal_fixture()
        # Linux default-terminate/core signals, including externally sent fault
        # signals. Uncatchable SIGKILL and stop/ignore/continue actions excluded.
        numbers = [signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGILL,
                   signal.SIGTRAP, signal.SIGABRT, signal.SIGBUS, signal.SIGFPE,
                   signal.SIGUSR1, signal.SIGSEGV, signal.SIGUSR2, signal.SIGPIPE,
                   signal.SIGALRM, signal.SIGTERM, signal.SIGSTKFLT, signal.SIGXCPU,
                   signal.SIGXFSZ, signal.SIGVTALRM, signal.SIGPROF, signal.SIGIO,
                   signal.SIGPWR, signal.SIGSYS, signal.SIGRTMIN, signal.SIGRTMAX]
        launcher = ('import os, resource, signal, sys; '
                    'resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); '
                    'signal.signal(int(sys.argv[1]), signal.SIG_DFL); '
                    'os.execv(sys.argv[2], sys.argv[2:])')
        for name in ('rsync', 'rsync-ssl'):
            shim = self.installed_shim(name, program)
            for number in numbers:
                with self.subTest(name=name, signal=number):
                    ready = self.root / f'{name}-{number}.ready'
                    wrapper = subprocess.Popen([sys.executable, '-c', launcher, str(int(number)),
                                                str(shim), str(int(number)), str(ready),
                                                'src', str(self.destination)],
                                               start_new_session=True, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE)
                    pids = []
                    try:
                        self.wait_file(ready)
                        native, descendant, group = json.loads(ready.read_text())
                        pids = [native, descendant]
                        self.assertEqual(group, wrapper.pid, 'native must stay in the job group')
                        self.assertEqual(os.getpgid(descendant), wrapper.pid)
                        wrapper.send_signal(number)
                        self.assertEqual(wrapper.wait(timeout=3), -number)
                        state = json.loads(self.state_file.read_text())
                        self.assertEqual(state['phase'], 'finished')
                        self.assertEqual(state['exit_code'], 128 + number)
                        self.assertIsNone(state['last_success_at'])
                        output, error = wrapper.communicate(timeout=3)
                        self.assertEqual(output, bytes([number]), error)
                        for pid in pids:
                            self.assertFalse(Path(f'/proc/{pid}').exists(), 'native tree not reaped')
                    finally:
                        # Negative controls must not leave orphan processes/pipes.
                        try:
                            os.killpg(wrapper.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        wrapper.communicate(timeout=3)

    def test_installed_shims_preserve_inherited_sigpipe_disposition(self):
        for name in ('rsync', 'rsync-ssl'):
            shim = self.installed_shim(name, '/usr/bin/printf')
            for ignored in (True, False):
                with self.subTest(name=name, ignored=ignored):
                    results = []
                    for program in ('/usr/bin/printf', str(shim)):
                        read_fd, write_fd = os.pipe()
                        os.close(read_fd)
                        try:
                            result = subprocess.run([program, 'hello'], stdout=write_fd,
                                                    stderr=subprocess.PIPE,
                                                    restore_signals=not ignored, timeout=5)
                            results.append((result.returncode, result.stderr))
                        finally:
                            os.close(write_fd)
                    self.assertEqual(results[1], results[0])

    def test_native_environment_is_exact_before_interpreter_or_shell_startup(self):
        environments = [
            {b'LANG': b'C', b'PATH': b'/usr/bin:/bin'},
            {b'LANG': b'C', b'LC_CTYPE': b'POSIX', b'PWD': b'/not-the-cwd',
             b'__keimirror_environment': b'caller value', b'__keimirror_fd': b'10',
             b'key': b'caller key', b'value': b'caller value',
             b'ODD-NAME': b'newlines\nnonutf8\xff=preserved',
             b'BASH_ENV': b'/must/not/be/read', b'SHLVL': b'42', b'_': b'original'},
            {},
            {b'LANG': b'C', b'LARGE': b'x' * 100000},
        ]
        driver = ('import sys; from telemetry.observe import run; '
                  'sys.exit(run(sys.argv[1], sys.argv[2], ["-0"], program="/usr/bin/env"))')
        commands = [[sys.executable, '-c', driver, str(self.config), str(self.state_dir)]]
        commands.extend([str(self.installed_shim(name, '/usr/bin/env')), '-0']
                        for name in ('rsync', 'rsync-ssl'))
        def mapping(raw):
            return dict(item.split(b'=', 1) for item in raw.split(b'\0') if item)
        for env in environments:
            direct = subprocess.run(['/usr/bin/env', '-0'], env=env, capture_output=True, check=True)
            for command in commands:
                with self.subTest(env=env, command=command[0]):
                    result = subprocess.run(command, env=env, cwd=REPO, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, b'')
                    self.assertEqual(mapping(result.stdout), mapping(direct.stdout))

    def test_installed_shims_preserve_argv_stdio_and_occupied_descriptors(self):
        from contextlib import ExitStack
        capture = self.root / 'shim-capture.json'
        program = self.executable(
            "import json, os, sys\n"
            "fds = {}\n"
            "for name in os.listdir('/proc/self/fd'):\n"
            "    try: fds[name] = os.readlink('/proc/self/fd/' + name)\n"
            "    except FileNotFoundError: pass\n"
            f"with open({str(capture)!r}, 'w') as stream: json.dump([sys.argv[1:], fds], stream)\n"
            "os.write(1, sys.stdin.buffer.read())\n"
            "os.write(2, b'native stderr\\n')\n"
        )
        arguments = ['--inherited-sigign', 'caller argument', '--', 'space \' quote', '']
        with ExitStack() as stack:
            files = [stack.enter_context((self.root / f'fd-{i}').open('w')) for i in range(70)]
            inherited = tuple(file.fileno() for file in files)
            for name in ('rsync', 'rsync-ssl'):
                with self.subTest(name=name):
                    shim = self.installed_shim(name, program)
                    result = subprocess.run([str(shim), *arguments], input=b'input\0\xff',
                                            capture_output=True, pass_fds=inherited, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, b'input\0\xff')
                    self.assertEqual(result.stderr, b'native stderr\n')
                    args, fds = json.loads(capture.read_text())
                    self.assertEqual(args, arguments)
                    self.assertEqual(set(map(int, fds)), {0, 1, 2, *inherited})
                    for file in files:
                        self.assertEqual(fds[str(file.fileno())], file.name)

    def test_cli_selects_exact_native_binary_and_preserves_arguments_after_separator(self):
        observe = self.observer()
        help_result = subprocess.run([sys.executable, str(REPO / "telemetry" / "observe.py"), "--help"],
                                     capture_output=True, text=True, timeout=5)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--config", help_result.stdout)
        self.assertIn("--state-dir", help_result.stdout)
        self.assertIn("--program", help_result.stdout)
        program = self.executable("raise SystemExit(7)\n")
        arguments = ["--config", "not-the-wrapper-config", "--", "src", str(self.destination)]
        selected = []

        def injected(config, state, args, *, program):
            selected.append((config, state, args, program))
            return observe.run(config, state, args, program=fake)

        fake = program
        result = observe.main(["--config", str(self.config), "--state-dir", str(self.state_dir),
                               "--program", "/usr/bin/rsync-ssl", "--", *arguments], runner=injected)
        self.assertEqual(result, 7)
        self.assertEqual(selected, [(str(self.config), str(self.state_dir), arguments, "/usr/bin/rsync-ssl")])

    def test_probes_dry_runs_and_serving_modes_never_replace_sync_state(self):
        observe = self.observer()
        program = self.executable("pass\n")
        observe.run(self.config, self.state_dir, ["src", str(self.destination)], program=program)
        before = self.state_file.read_bytes()
        marker = self.root / "executed"
        program = self.executable(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(23)\n")
        for option in ("--dry-run", "-n", "-avn", "--list-only", "--version", "-V", "--help",
                       "--server", "--sender", "--daemon"):
            with self.subTest(option=option):
                marker.unlink(missing_ok=True)
                self.assertEqual(observe.run(self.config, self.state_dir,
                                            [option, "src", str(self.destination)], program=program), 23)
                self.assertTrue(marker.exists(), "excluded observations still execute their native binary")
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_native_signal_results_and_unexecutable_programs_are_preserved(self):
        observe = self.observer()
        for signum in (signal.SIGTERM, signal.SIGKILL, signal.SIGPIPE):
            with self.subTest(signum=signum):
                setup = "" if signum == signal.SIGKILL else f"signal.signal({int(signum)}, signal.SIG_DFL)\n"
                program = self.executable("import os, signal\n" + setup + f"os.kill(os.getpid(), {int(signum)})\n")
                wrapper = self.start_wrapper(program, ["src", str(self.destination)])
                self.assertEqual(wrapper.wait(timeout=5), -signum)
                state = json.loads(self.state_file.read_text())
                self.assertEqual(state["exit_code"], 128 + signum)
                self.assertEqual(state["phase"], "finished")
        program = self.executable("pass\n")
        program.chmod(0o644)
        try:
            result = observe.run(self.config, self.state_dir, ["src", str(self.destination)], program=program)
        except OSError as error:
            result = type(error).__name__
        self.assertEqual(result, 126)
        self.assertEqual(json.loads(self.state_file.read_text())["exit_code"], 126)

    def test_inherited_ignored_termination_signal_remains_ignored_by_native_binary(self):
        self.observer()
        program = self.executable("import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\nraise SystemExit(7)\n")
        driver = ("import signal, sys; from telemetry.observe import run; "
                  "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                  "raise SystemExit(run(sys.argv[1], sys.argv[2], sys.argv[4:], program=sys.argv[3]))")
        result = subprocess.run([sys.executable, "-c", driver, str(self.config), str(self.state_dir),
                                 str(program), "src", str(self.destination)], capture_output=True,
                                cwd=REPO, timeout=5)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(json.loads(self.state_file.read_text())["exit_code"], 7)

    def test_nonregular_or_oversized_telemetry_sources_cannot_block_transfers(self):
        observe = self.observer()
        program = self.executable("raise SystemExit(23)\n")
        original_config = self.config
        fifo = self.root / "config-fifo"
        os.mkfifo(fifo)
        self.state_file.parent.mkdir(parents=True)
        for which in ("config", "state"):
            with self.subTest(which=which):
                if which == "config":
                    self.config = fifo
                else:
                    self.config = original_config
                    os.mkfifo(self.state_file)
                wrapper = self.start_wrapper(program, ["src", str(self.destination)])
                try:
                    try:
                        result = wrapper.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        result = None
                    self.assertEqual(result, 23, "invalid telemetry source must not block native execution")
                finally:
                    if wrapper.poll() is None:
                        wrapper.kill()
                    wrapper.communicate(timeout=5)
                    self.state_file.unlink(missing_ok=True)
        self.config = original_config
        success = self.executable("pass\n", "success")
        observe.run(self.config, self.state_dir, ["src", str(self.destination)], program=success)
        original = self.state_file.read_text()
        outside = self.root / "untrusted-state"
        outside.write_text(original)
        self.state_file.unlink()
        self.state_file.symlink_to(outside)
        self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)], program=program), 23)
        self.assertIsNone(json.loads(self.state_file.read_text())["last_success_at"])
        self.assertEqual(outside.read_text(), original)
        self.state_file.write_text(original + " " * 65536)
        self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)], program=program), 23)
        self.assertIsNone(json.loads(self.state_file.read_text())["last_success_at"])

    def test_delayed_older_start_cannot_replace_a_newer_run(self):
        self.observer()
        ready = self.root / "older-clock-ready"
        release = self.root / "release-older-clock"
        program = self.executable("raise SystemExit(23)\n")
        driver = (
            "import sys, time\nfrom pathlib import Path\nfrom telemetry.observe import run\n"
            "def clock():\n"
            f"    Path({str(ready)!r}).touch()\n"
            "    deadline = time.monotonic() + 5\n"
            f"    while not Path({str(release)!r}).exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
            "    return time.time()\n"
            "raise SystemExit(run(sys.argv[1], sys.argv[2], sys.argv[4:], program=sys.argv[3], now=clock))\n"
        )
        older = subprocess.Popen([sys.executable, "-c", driver, str(self.config), str(self.state_dir),
                                  str(program), "src", str(self.destination)],
                                 cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_file(ready)
            newer = self.start_wrapper(program, ["src", str(self.destination)])
            self.assertEqual(newer.wait(timeout=5), 23)
            expected = self.state_file.read_bytes()
            release.touch()
            self.assertEqual(older.wait(timeout=5), 23)
            self.assertEqual(self.state_file.read_bytes(), expected)
        finally:
            release.touch()
            older.communicate(timeout=6)

    def test_duplicate_json_keys_cannot_launder_a_prior_success(self):
        observe = self.observer()
        success = self.executable("pass\n", "success")
        observe.run(self.config, self.state_dir, ["src", str(self.destination)], program=success)
        valid = self.state_file.read_text()
        failure = self.executable("raise SystemExit(23)\n", "failure")
        for field, earlier in (("exit_code", "23"), ("repository", '"kali"'), ("last_success_at", "null")):
            with self.subTest(field=field):
                malformed = valid.replace(f'"{field}":', f'"{field}":{earlier},"{field}":', 1)
                self.state_file.write_text(malformed)
                self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)], program=failure), 23)
                self.assertIsNone(json.loads(self.state_file.read_text())["last_success_at"])

    def test_late_write_failure_and_busy_metadata_lock_preserve_native_failure(self):
        from unittest import mock
        import fcntl

        observe = self.observer()
        program = self.executable("raise SystemExit(23)\n")
        replace = os.replace
        writes = []

        def fail_terminal_write(source, target):
            writes.append(target)
            if len(writes) == 2:
                raise PermissionError("fixture read-only filesystem")
            return replace(source, target)

        with mock.patch.object(observe.os, "replace", side_effect=fail_terminal_write):
            self.assertEqual(observe.run(self.config, self.state_dir, ["src", str(self.destination)], program=program), 23)
        self.assertEqual(len(writes), 2)
        incomplete = json.loads(self.state_file.read_text())
        self.assertEqual(incomplete["phase"], "running")
        self.assertIsNone(incomplete["exit_code"])
        self.assertIsNone(incomplete["last_success_at"])
        self.assertEqual(list(self.state_file.parent.glob("*.tmp")), [])
        before = self.state_file.read_bytes()
        with (self.state_file.parent / ".state.lock").open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            wrapper = self.start_wrapper(program, ["src", str(self.destination)])
            self.assertEqual(wrapper.wait(timeout=2), 23)
        self.assertEqual(self.state_file.read_bytes(), before)

    def test_exact_argv_environment_streams_and_lock_descriptor(self):
        self.observer()
        capture = self.root / "capture.json"
        program = self.executable(
            "import json, os, sys\n"
            "fd = int(os.environ['FIXTURE_LOCK_FD'])\n"
            "os.fstat(fd)\n"
            "with open(os.environ['FIXTURE_CAPTURE'], 'w') as f:\n"
            "    json.dump({'args': sys.argv[1:], 'env': dict(os.environ)}, f)\n"
            "os.write(1, sys.stdin.buffer.read())\n"
            "os.write(2, b'unchanged stderr\\n')\n"
            "sys.exit(23)\n"
        )
        arguments = ["--delete", "--exclude=space & quote'", "rsync://upstream.invalid/repo", str(self.destination)]
        with (self.root / "job.lock").open("w") as lock:
            env = dict(os.environ, FIXTURE_LOCK_FD=str(lock.fileno()),
                       FIXTURE_CAPTURE=str(capture), FIXTURE_UNCHANGED="a b\n'$")
            driver = ("import sys; from telemetry.observe import run; "
                      "raise SystemExit(run(sys.argv[1], sys.argv[2], sys.argv[4:], program=sys.argv[3]))")
            result = subprocess.run(
                [sys.executable, "-c", driver, str(self.config), str(self.state_dir), str(program), *arguments],
                input=b"stdin\x00unchanged\xff\n", capture_output=True,
                env=env, pass_fds=(lock.fileno(),), cwd=REPO, timeout=5,
            )
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout, b"stdin\x00unchanged\xff\n")
        self.assertEqual(result.stderr, b"unchanged stderr\n")
        captured = json.loads(capture.read_text())
        self.assertEqual(captured["args"], arguments)
        self.assertEqual(captured["env"], env)


if __name__ == "__main__":
    unittest.main()
