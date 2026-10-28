"""Transparent, fail-open observation of existing rsync invocations."""
from contextlib import contextmanager
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time
import uuid


REPOSITORIES = frozenset({
    "archlinux", "gentoo", "gentoo-portage", "kali", "kali-images", "cachyos",
    "artix", "artix-iso", "artix-weekly-iso", "opnsense", "endeavouros",
    "fyralabs", "manjaro", "proxmox",
})


def repository_for(config, arguments):
    """Match only the final absolute destination, never a source or option."""
    if len(arguments) < 2 or not os.path.isabs(arguments[-1]):
        return None
    excluded = {"--dry-run", "--list-only", "--version", "--help", "--server", "--sender", "--daemon"}
    for argument in arguments:
        if argument == "--":
            break
        if argument.split("=", 1)[0] in excluded:
            return None
        if argument.startswith("-") and not argument.startswith("--") and any(flag in argument for flag in "nV"):
            return None
    destination = Path(arguments[-1]).resolve()
    matches = []
    for repository, entry in config["repositories"].items():
        if repository not in REPOSITORIES or not os.path.isabs(entry["path"]):
            continue
        root = Path(entry["path"]).resolve()
        if destination.is_relative_to(root):
            matches.append((len(root.parts), repository))
    matches.sort(reverse=True)
    if not matches or (len(matches) > 1 and matches[0][0] == matches[1][0]):
        return None
    return matches[0][1]


def timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def valid_state(state, repository, at):
    """Do not recover success from an unverified fragment or incoherent state."""
    try:
        if (type(state["schema_version"]) is not int or state["schema_version"] != 1
                or state["repository"] != repository
                or type(state["pid"]) is not int or state["pid"] <= 0
                or type(state["start_ticks"]) is not int or state["start_ticks"] < 0
                or str(uuid.UUID(state["boot_id"])) != state["boot_id"]
                or not timestamp(state["started_at"]) or state["started_at"] > at):
            return False
        success = state["last_success_at"]
        if success is not None and (not timestamp(success) or success > at):
            return False
        if state["phase"] == "running":
            return (state["finished_at"] is None and state["exit_code"] is None
                    and (success is None or success <= state["started_at"]))
        if (state["phase"] != "finished" or not timestamp(state["finished_at"])
                or not state["started_at"] <= state["finished_at"] <= at
                or type(state["exit_code"]) is not int or not 0 <= state["exit_code"] <= 255):
            return False
        if state["exit_code"] == 0:
            return success == state["finished_at"]
        return success is None or success <= state["started_at"]
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def read_json(path, limit=65536):
    """Only bounded regular files; an accidental FIFO cannot hold up a sync."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid telemetry file")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("telemetry file too large")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate telemetry key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique_keys)


def ensure_directory(path):
    """Create only missing directories; never chmod provisioned job directories."""
    path = Path(path)
    if path.is_dir():
        return
    ensure_directory(path.parent)
    try:
        path.mkdir(mode=0o755)
    except FileExistsError:
        if not path.is_dir():
            raise
    else:
        path.chmod(0o755)


def atomic_json(path, data):
    """Readers see a whole old/new inode, with private root-readable permissions."""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o640)
            json.dump(data, stream, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


@contextmanager
def exclusive_lock(path):
    """Nonblocking, close-on-exec advisory lock; never lock the transfer itself."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o640)
    try:
        os.fchmod(fd, 0o640)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _begin(config_path, state_dir, arguments, now):
    entered_at = time.time()
    config = read_json(config_path)
    repository = repository_for(config, arguments)
    if repository is None:
        return None
    path = Path(state_dir) / "runs" / repository / "state.json"
    ensure_directory(path.parent)
    stat = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
    state = {
        "schema_version": 1, "repository": repository, "phase": "running",
        "pid": os.getpid(), "start_ticks": int(stat[19]),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "started_at": now(), "finished_at": None, "exit_code": None, "last_success_at": None,
    }
    with exclusive_lock(path.parent / ".state.lock"):
        try:
            previous = read_json(path)
            if valid_state(previous, repository, state["started_at"]):
                if (previous["boot_id"] == state["boot_id"]
                        and previous["start_ticks"] > state["start_ticks"]
                        and previous["started_at"] >= entered_at):
                    return None
                state["last_success_at"] = previous["last_success_at"]
        except (OSError, ValueError):
            pass
        atomic_json(path, state)
    return path, state


def _finish(observation, result, now):
    path, state = observation
    with exclusive_lock(path.parent / ".state.lock"):
        current = read_json(path)
        identity = ("pid", "start_ticks", "boot_id", "started_at")
        if current.get("phase") != "running" or any(current.get(key) != state[key] for key in identity):
            return
        finished = now()
        state.update(phase="finished", finished_at=finished, exit_code=result if result >= 0 else 128 - result,
                     last_success_at=finished if result == 0 else state["last_success_at"])
        atomic_json(path, state)


def _signal_tree(pid, number):
    """Signal only verified descendants, never the caller's shared job group.

    Pin identities before delivery so exiting/reparented processes or PID reuse
    cannot redirect a signal to another job. Children are signalled first.
    """
    handles = []
    seen = set()

    def collect(current, parent=None):
        if current in seen:
            return
        seen.add(current)
        fd = None
        try:
            before = Path(f"/proc/{current}/stat").read_text().rsplit(")", 1)[1].split()
            if parent is not None and int(before[1]) != parent:
                return
            fd = os.pidfd_open(current)
            after = Path(f"/proc/{current}/stat").read_text().rsplit(")", 1)[1].split()
            if before[19] != after[19] or before[1] != after[1]:
                return
            handles.append(fd)
            fd = None
            for task in Path(f"/proc/{current}/task").iterdir():
                try:
                    children = (task / "children").read_text().split()
                except OSError:
                    continue
                for descendant in children:
                    collect(int(descendant), current)
        except (OSError, ValueError, IndexError):
            pass
        finally:
            if fd is not None:
                os.close(fd)

    try:
        collect(pid, os.getpid())
        for fd in reversed(handles):
            try:
                signal.pidfd_send_signal(fd, number)
            except ProcessLookupError:
                pass
    finally:
        for fd in handles:
            os.close(fd)


def _original_environment(stream):
    """Read the kernel's pre-startup environment as bounded, raw NUL records."""
    limit = max(131072, os.sysconf("SC_ARG_MAX"))
    raw = stream.read(limit + 1)
    if len(raw) > limit or (raw and not raw.endswith(b"\0")):
        raise ValueError("invalid original environment")
    return dict(entry.split(b"=", 1) for entry in raw.split(b"\0") if entry)


def _execute(program, arguments, *, inherited_sigign=None, native_environment=None):
    child = None
    pending = []
    previous = {}

    def forward(number, frame=None):
        if child is None:
            pending.append(number)
        else:
            try:
                _signal_tree(child.pid, number)
            except ProcessLookupError:
                pass

    try:
        # Only the installed shim can know dispositions before Python startup.
        if inherited_sigign is not None:
            for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
                number = getattr(signal, name, None)
                if number is not None:
                    disposition = signal.SIG_IGN if inherited_sigign & (1 << (number - 1)) else signal.SIG_DFL
                    previous[number] = signal.signal(number, disposition)
        # Linux signals whose default action is terminate/core, including RT.
        # Leave job control and default-ignore signals alone. Only externally
        # delivered fault signals are supported: a genuine synchronous fault
        # inside Python/C can loop before a Python handler gets to run.
        # PID-only SIGKILL cannot be caught: kill the job group/cgroup instead;
        # an interrupted observation remains running, never fabricated success.
        nonterminating = {signal.SIGCHLD, signal.SIGURG, signal.SIGWINCH,
                          signal.SIGCONT, signal.SIGTSTP, signal.SIGTTIN,
                          signal.SIGTTOU, signal.SIGSTOP, signal.SIGKILL}
        for number in sorted(signal.valid_signals() - nonterminating):
            # Python itself ignores SIGPIPE; Popen restores it for the native exec.
            # Preserve inherited ignores such as nohup's SIGHUP disposition.
            if (number != signal.SIGPIPE or inherited_sigign is not None) and signal.getsignal(number) == signal.SIG_IGN:
                continue
            handler = signal.signal(number, forward)
            previous.setdefault(number, handler)
        # SIGCHLD=SIG_IGN auto-reaps children: Popen then reports a spurious
        # zero status on ECHILD. Only the observer must reset it; native exec
        # must retain the caller's ignore. This standalone observer is strictly
        # single-threaded. The exceptional child hook does only that reset;
        # the ordinary path retains Popen's safe posix_spawn fast path. Do not
        # embed this signal-owning runner in a multithreaded process.
        child_setup = None
        if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
            previous[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            def restore_sigchld_ignore():
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            child_setup = restore_sigchld_ignore
        try:
            child = subprocess.Popen([str(program), *arguments], close_fds=False,
                                     restore_signals=inherited_sigign is None, env=native_environment,
                                     preexec_fn=child_setup)
        except FileNotFoundError:
            return 127
        except OSError:
            return 126
        for number in pending:
            forward(number)
        return child.wait()
    finally:
        if child is not None and child.poll() is None:
            _signal_tree(child.pid, signal.SIGKILL)
            child.wait()
        for number, handler in previous.items():
            signal.signal(number, handler)


def exit_with_status(result):
    """Keep POSIX signal termination observable to the calling sync script."""
    if result < 0:
        if -result != signal.SIGKILL:
            signal.signal(-result, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {-result})
        os.kill(os.getpid(), -result)
    raise SystemExit(result)


def run(config_path, state_dir, arguments, *, program="/usr/bin/rsync", now=time.time,
        inherited_sigign=None, native_environment=None):
    """Execute the original binary; telemetry exceptions never change its result."""
    if native_environment is None:
        with open("/proc/self/environ", "rb") as stream:
            native_environment = _original_environment(stream)
    observation = None
    try:
        observation = _begin(config_path, state_dir, arguments, now)
    except Exception:
        pass
    result = _execute(program, arguments, inherited_sigign=inherited_sigign,
                      native_environment=native_environment)
    try:
        if observation is not None:
            _finish(observation, result, now)
    except Exception:
        pass
    return result


def main(argv=None, *, runner=run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--program", choices=("/usr/bin/rsync", "/usr/bin/rsync-ssl"),
                        default="/usr/bin/rsync")
    parser.add_argument("--inherited-sigign", type=lambda value: int(value, 16), help=argparse.SUPPRESS)
    parser.add_argument("--original-environment-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--snapshot-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("arguments", nargs=argparse.REMAINDER, metavar="-- ARGS")
    options = parser.parse_args(argv)
    arguments = options.arguments
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    context = {}
    if options.original_environment_fd is not None:
        with os.fdopen(options.original_environment_fd, "rb") as stream:
            context["native_environment"] = _original_environment(stream)
        if options.snapshot_pid is not None:
            try:
                os.waitpid(options.snapshot_pid, 0)
            except ChildProcessError:
                pass  # Bash may already have reaped a small snapshot writer.
    if options.inherited_sigign is not None:
        context["inherited_sigign"] = options.inherited_sigign
    return runner(options.config, options.state_dir, arguments, program=options.program, **context)


if __name__ == "__main__":
    exit_with_status(main())
