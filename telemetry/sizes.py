"""One sequential, low-priority sweep of allocated repository disk usage."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time

if __package__:
    from .observe import REPOSITORIES, atomic_json, ensure_directory, exclusive_lock, read_json, timestamp
else:
    from observe import REPOSITORIES, atomic_json, ensure_directory, exclusive_lock, read_json, timestamp


def _measure(root, program, timeout):
    command = ["/usr/bin/ionice", "-c", "3", "/usr/bin/nice", "-n", "19", str(program),
               "--summarize", "--block-size=1", "--one-file-system", "--", str(root)]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, process_group=0) as child:
        try:
            output, _ = child.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            raise
    number, separator, output_path = output.partition(b"\t")
    if (child.returncode != 0 or not separator or not number.isdigit()
            or len(number) > 16 or output_path != os.fsencode(root) + b"\n"):
        raise ValueError("invalid measurement")
    size = int(number)
    if size > 9007199254740991:
        raise ValueError("measurement exceeds safe integer range")
    return size


def scan(config_path, state_dir, *, program="/usr/bin/du", timeout=120, now=time.time):
    try:
        if not timestamp(timeout) or timeout <= 0:
            return 1
        config = read_json(config_path)
        repositories = config["repositories"]
        if not isinstance(repositories, dict) or not repositories or set(repositories) - REPOSITORIES:
            return 1
        for entry in repositories.values():
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not os.path.isabs(entry["path"]):
                return 1
        state_dir = Path(state_dir)
        ensure_directory(state_dir)
        with exclusive_lock(state_dir / ".sizes.lock"):
            return _sweep(config, state_dir, program, timeout, now)
    except BlockingIOError:
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return 1


def _load_cache(path, repositories):
    cache = {"schema_version": 1, "repositories": {}}
    try:
        previous = read_json(path)
        if type(previous["schema_version"]) is not int or previous["schema_version"] != 1:
            return cache
        records = previous["repositories"]
        for repository in repositories:
            record = records.get(repository)
            if (not isinstance(record, dict) or type(record.get("error")) is not bool
                    or type(record.get("bytes")) is not int or not 0 <= record["bytes"] <= 9007199254740991
                    or not timestamp(record.get("measured_at"))):
                continue
            cache["repositories"][repository] = {key: record[key] for key in ("bytes", "measured_at", "error")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        pass
    return cache


def _sweep(config, state_dir, program, timeout, now):
    cache = _load_cache(state_dir / "sizes.json", config["repositories"])
    failed = False
    for repository, entry in config["repositories"].items():
        try:
            root = Path(entry["path"]).resolve(strict=True)
            if not root.is_dir():
                raise ValueError("repository root must be a directory")
            size = _measure(root, program, timeout)
            measured_at = now()
            if not timestamp(measured_at):
                raise ValueError("invalid measurement timestamp")
            record = {"bytes": size, "measured_at": measured_at, "error": False}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            record = dict(cache["repositories"].get(repository, {"bytes": None, "measured_at": None}), error=True)
            failed = True
        cache["repositories"][repository] = record
        atomic_json(state_dir / "sizes.json", cache)
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--timeout", type=float, default=120,
                        help="maximum seconds per repository (default: 120)")
    options = parser.parse_args(argv)
    if not timestamp(options.timeout) or options.timeout <= 0:
        parser.error("--timeout must be a finite positive number")
    return scan(options.config, options.state_dir, timeout=options.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
