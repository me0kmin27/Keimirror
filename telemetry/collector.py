#!/usr/bin/env python3
"""One local, privacy-safe mirror telemetry tick (stdlib only).

Deployment: trusted config/code and a root-owned private state directory; only the
atomic 0644 output belongs in the public directory. HTTP JSON `project` labels
must be the fourteen repository IDs (not lossy ten-project labels).

Limits: 256 KiB per log/tick, 64 KiB initial sync-log tail, 2 MiB JSON inputs,
8192 distinct delivery timestamp/repository pairs, 16384 /proc entries, 512
OPNsense log-directory entries, 2 MiB/4096 sockets per TCP sample (2 seconds),
and a 10-second CLI deadline. No du/tree scan, remote network request, or collector-owned
HTTP server. Live HTTP counters use a bounded local Unix socket. Tick-level errors leave the public snapshot untouched; missing individual
sources yield null/unknown metrics.

Delivery cursors start at EOF, never replay a historical raw tail, and reset
60-second coverage on rotation, detectable truncation, read gaps >30 seconds,
malformed rows or exhausted budgets. A short content anchor detects regrowth;
identical-prefix copytruncate is inherently ambiguous, so prefer rename rotation.
Rsync prefix timestamps were removed by the supplied audit. Its counter is the
trailing *observed* window of newly completed numeric XFER/send records, not a
historic timestamp reconstruction or NIC total. HTTP uses completed-request ts.

Transfer rates combine attributed upstream/delivery sockets with live HTTP counters;
missing coverage stays null. No NIC totals or progress2 reconstructed bytes are
substituted. Kali completion
requires same-run stage1/stage2 return codes plus Mirrorsync done; yearless log
dates use the audited Asia/Seoul timezone and log mtime only as a year anchor.
Trace/Arch upstream timestamps and Kali-images statistics alone are not success.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
import pwd
import re
import signal
import stat
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeGuard

if __package__:
    from . import rates, http_rates, private_reasons
else:  # Installed collector.py is also a directly executable CLI.
    import rates
    import http_rates
    import private_reasons

PROJECTS = {
    "archlinux": ["archlinux"], "gentoo": ["gentoo", "gentoo-portage"],
    "kali": ["kali", "kali-images"], "cachyos": ["cachyos"],
    "artix": ["artix", "artix-iso", "artix-weekly-iso"],
    "opnsense": ["opnsense"], "endeavouros": ["endeavouros"],
    "fyralabs": ["fyralabs"], "manjaro": ["manjaro"], "proxmox": ["proxmox"],
}
REPOSITORIES = tuple(r for ids in PROJECTS.values() for r in ids)


def empty_metrics() -> dict[str, Any]:
    return {
        "state": "unknown", "last_attempt_at": None, "last_finished_at": None,
        "last_success_at": None, "exit_code": None, "warning": False,
        "size_bytes": None, "size_measured_at": None,
        "sync_rx_bps": None, "sync_tx_bps": None, "rates_observed_at": None,
        "http_bytes_60s": None, "rsync_bytes_60s": None, "traffic_observed_since": None,
    }


def open_evidence(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not regular evidence")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_bounded(path, limit):
    with open_evidence(path) as stream:
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("evidence exceeds bound")
        return data


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path) -> Any:
    try:
        return json.loads(read_bounded(path, 2 * 1024 * 1024), object_pairs_hook=unique_keys)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


MAX_SAFE_INTEGER = 2**53 - 1


def byte_count(value):
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def timestamp(value: Any, now: float) -> TypeGuard[int | float]:
    return type(value) in (int, float) and 0 <= value <= now and math.isfinite(value)


def valid_observer(record: Any, rid: str, now: float) -> TypeGuard[dict[str, Any]]:
    if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
            or record.get("schema_version") != 1 or record.get("repository") != rid
            or type(record.get("pid")) is not int or record["pid"] <= 0
            or type(record.get("start_ticks")) is not int or record["start_ticks"] < 0):
        return False
    try:
        if str(uuid.UUID(record.get("boot_id"))) != record["boot_id"]:
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    if record.get("phase") not in ("running", "finished") or not timestamp(record.get("started_at"), now):
        return False
    success = record.get("last_success_at")
    if success is not None and not timestamp(success, now):
        return False
    if record["phase"] == "finished":
        rc, end = record.get("exit_code"), record.get("finished_at")
        return (type(rc) is int and 0 <= rc <= 255 and timestamp(end, now)
                and end >= record["started_at"]
                and (success == end if rc == 0 else success is None or success <= record["started_at"]))
    return (record.get("exit_code") is None and record.get("finished_at") is None
            and (success is None or success <= record["started_at"]))


def atomic_json(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(data, stream, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def process_identity(directory):
    """stat comm may contain spaces/parentheses; starttime is field 22."""
    try:
        fields = read_bounded(Path(directory) / "stat", 8192).decode().rsplit(")", 1)[1].split()
        if fields[0] in ("Z", "X", "x"):
            return None
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def observer_live(record, proc_root):
    if (type(record.get("pid")) is not int or record["pid"] <= 0
            or type(record.get("start_ticks")) is not int or record["start_ticks"] < 0):
        return False
    try:
        boot = read_bounded(Path(proc_root) / "sys/kernel/random/boot_id", 128).decode().strip()
    except (OSError, ValueError, UnicodeError):
        return False
    return (bool(boot) and record.get("boot_id") == boot
            and process_identity(Path(proc_root) / str(record["pid"])) == record["start_ticks"])


def observer_metrics(record, rid, now, proc_root):
    result = empty_metrics()
    if not valid_observer(record, rid, now):
        return result
    result["last_attempt_at"] = record.get("started_at")
    result["last_success_at"] = record.get("last_success_at")
    if record["phase"] == "running" and observer_live(record, proc_root):
        result["state"] = "syncing"
    if record.get("phase") == "finished":
        result["last_finished_at"] = record.get("finished_at")
        result["exit_code"] = record.get("exit_code")
        result["state"] = "completed" if result["exit_code"] == 0 else "failed"
    return result


def matching_processes(config, proc_root, now):
    """Read only bounded /proc metadata, never locks or daemon serving jobs."""
    proc_root = Path(proc_root)
    active = {}
    try:
        boot_time = next(float(line.split()[1]) for line in read_bounded(proc_root / "stat", 256 * 1024).decode().splitlines()
                         if line.startswith("btime "))
        hz = os.sysconf("SC_CLK_TCK")
        with os.scandir(proc_root) as entries:
            directories = []
            for index, entry in enumerate(entries):
                if index >= 16384:
                    break
                if entry.name.isdecimal():
                    directories.append(Path(entry.path))
    except (OSError, ValueError, StopIteration):
        return active
    jobs = []
    for rid in REPOSITORIES:
        job = config["repositories"][rid]
        try:
            uid = pwd.getpwnam(job["user"]).pw_uid
        except KeyError:
            continue
        roots = {os.path.normpath(job["path"]), os.path.realpath(job["path"])}
        jobs.append((rid, job["script"], uid, roots))
    for directory in directories:
        ticks = process_identity(directory)
        if ticks is None:
            continue
        try:
            with open_evidence(directory / "cmdline") as stream:
                raw = stream.read(16385)
            if len(raw) > 16384 or not raw.endswith(b"\0"):
                continue
            args = raw[:-1].decode("utf-8").split("\0")
            uid = directory.stat().st_uid
            if not args or not args[0] or process_identity(directory) != ticks:
                continue
        except (OSError, UnicodeError):
            continue
        executable = os.path.basename(args[0])
        for rid, script, expected_uid, roots in jobs:
            if uid != expected_uid:
                continue
            scripted = args[0] == script or (executable in ("bash", "sh", "dash", "perl", "python3")
                                             and len(args) > 1 and args[1] == script)
            rsync = executable in ("rsync", "rsync-ssl") and not any(
                a == "--server" or a == "--daemon" or a.startswith("--daemon=") for a in args)
            dest = args[-1]
            destination = (rsync and os.path.isabs(dest) and ".." not in Path(dest).parts
                           and any(os.path.normpath(dest) == root or os.path.normpath(dest).startswith(root + "/")
                                   for root in roots))
            if scripted or destination:
                started = boot_time + ticks / hz
                if timestamp(started, now):
                    active[rid] = min(active.get(rid, started), started)
    return active


MAX_READ = 256 * 1024
BOOTSTRAP_READ = 64 * 1024
MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def shell_date(value):
    """The two audited GNU date formats, without locale or TZ dependence."""
    parts = value.split()
    try:
        if len(parts) == 7 and parts[1] in MONTHS:
            _, month, day, clock, ampm, zone, year = parts
        elif len(parts) == 7 and parts[2] in MONTHS:
            _, day, month, year, clock, ampm, zone = parts
        else:
            return None
        hour, minute, second = map(int, clock.split(":"))
        if ampm not in ("AM", "PM") or not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "PM" else 0)
        offset = {"KST": 9, "UTC": 0, "GMT": 0}[zone]
        return datetime(int(year), MONTHS[month], int(day), hour, minute, second,
                        tzinfo=timezone(timedelta(hours=offset))).timestamp()
    except (ValueError, KeyError):
        return None


def valid_log_result(result, now, kind):
    return (isinstance(result, dict) and set(result) == set(empty_metrics())
            and result.get("state") in ("unknown", "failed", "completed")
            and type(result.get("warning")) is bool
            and (kind in ("kali", "opnsense") or result["state"] != "completed" and result["last_success_at"] is None)
            and (result.get("exit_code") is None or type(result["exit_code"]) is int and 0 <= result["exit_code"] <= 255)
            and all(result.get(f) is None or timestamp(result[f], now)
                    for f in ("last_attempt_at", "last_finished_at", "last_success_at"))
            and all(result.get(f) is None for f in ("size_bytes", "size_measured_at", "sync_rx_bps", "sync_tx_bps",
                                                   "rates_observed_at", "http_bytes_60s", "rsync_bytes_60s", "traffic_observed_since")))


def parse_kali_line(line, result, context, now, anchor):
    match = re.fullmatch(r"([A-Z][a-z]{2}) +([0-9]{1,2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) \S+ ftpsync-kali\[([0-9]+)\]: (.*)", line)
    if not match:
        return result
    month, day, hour, minute, second, pid, message = match.groups()
    try:
        local = timezone(timedelta(hours=9))  # Both audited hosts use Asia/Seoul.
        year = datetime.fromtimestamp(anchor, local).year
        candidates = []
        for y in (year - 1, year, year + 1):
            try:
                candidates.append(datetime(y, MONTHS[month], int(day), int(hour), int(minute), int(second),
                                           tzinfo=local).timestamp())
            except (ValueError, OverflowError):
                continue
        when = min(candidates, key=lambda value: abs(value - anchor))
        pid = int(pid)
    except (ValueError, KeyError, OverflowError):
        return result
    if not timestamp(when, now):
        return result
    if message == "Mirrorsync start":
        success = result["last_success_at"]
        result = empty_metrics()
        result["last_attempt_at"], result["last_success_at"] = when, success
        context.clear()
        context.update(pid=pid, stage=None, stage1=None, stage2=None, failure=None)
        return result
    if context.get("pid") != pid or result["last_attempt_at"] is None or when < result["last_attempt_at"]:
        return result
    stage = re.match(r"Running stage([12]): ", message)
    if stage:
        context["stage"] = int(stage[1])
        context["stage" + stage[1]] = None
        if stage[1] == "1":
            context["stage2"] = None
    returned = re.fullmatch(r"Back from rsync with returncode ([0-9]{1,3})", message)
    if returned and context.get("stage") in (1, 2):
        rc = int(returned[1])
        if rc <= 255:
            context["stage" + str(context["stage"])] = rc
            if rc not in (0, 24):
                context["failure"] = rc
                result["state"], result["exit_code"] = "failed", rc
    if message in ("Mirrorsync done", "Mirrorsync done with errors"):
        result["last_finished_at"] = when
        if context.get("failure") is not None or message.endswith("with errors"):
            result["state"], result["exit_code"] = "failed", context.get("failure")
        elif context.get("stage1") in (0, 24) and context.get("stage2") in (0, 24):
            warning = 24 in (context["stage1"], context["stage2"])
            result.update(state="completed", exit_code=24 if warning else 0, warning=warning,
                          last_success_at=when)
    return result


def valid_kali_context(context):
    return isinstance(context, dict) and (not context or (
        set(context) == {"pid", "stage", "stage1", "stage2", "failure"}
        and type(context["pid"]) is int and context["pid"] > 0
        and context["stage"] in (None, 1, 2)
        and all(context[k] is None or type(context[k]) is int and 0 <= context[k] <= 255
                for k in ("stage1", "stage2", "failure"))))


def parse_sync_line(line, result, kind, now, context, anchor):
    if kind == "kali":
        return parse_kali_line(line, result, context, now, anchor)
    if kind in ("opnsense", "opnsense-pending"):
        start = re.fullmatch(r"(.+) Started update: .*", line)
        finish = re.fullmatch(r"(.+) Finished update\.", line)
        if start:
            result = empty_metrics()
            when = shell_date(start[1])
            if timestamp(when, now):
                result["last_attempt_at"] = when
            return result
        if finish and kind == "opnsense":
            when = shell_date(finish[1])
            if (timestamp(when, now) and when >= (result["last_attempt_at"] or 0)
                    and result["state"] != "failed"):
                result.update(state="completed", exit_code=0, last_finished_at=when, last_success_at=when)
            return result
    if line.startswith("Started update at "):
        started = shell_date(line[len("Started update at "):])
        result = empty_metrics()
        if timestamp(started, now):
            result["last_attempt_at"] = started
    elif line.startswith("End: "):
        finished = shell_date(line[5:])
        if timestamp(finished, now) and finished >= (result["last_attempt_at"] or 0):
            result["last_finished_at"] = finished
    else:
        failure = re.match(r"rsync (?:error|warning): .*\(code ([0-9]{1,3})\)", line)
        if failure and 0 < int(failure[1]) <= 255:
            result["state"], result["exit_code"] = "failed", int(failure[1])
    return result


def sync_log(path, previous, kind, now, max_read):
    """A bounded bootstrap tail; then only new complete CR/LF records."""
    if not path:
        return empty_metrics(), None
    try:
        with open_evidence(path) as stream:
            info = os.fstat(stream.fileno())
            continuing = (isinstance(previous, dict) and previous.get("ino") == info.st_ino
                          and previous.get("dev") == info.st_dev and byte_count(previous.get("offset"))
                          and previous["offset"] <= info.st_size
                          and info.st_size - previous["offset"] <= max_read
                          and valid_log_result(previous.get("result"), now, kind)
                          and valid_kali_context(previous.get("context"))
                          and previous.get("anchor") == cursor_anchor(stream, previous["offset"]))
            offset = previous["offset"] if continuing else max(0, info.st_size - min(BOOTSTRAP_READ, max_read))
            result = dict(previous["result"]) if continuing else empty_metrics()
            context = dict(previous["context"]) if continuing else {}
            stream.seek(offset)
            data = stream.read(min(max_read, info.st_size - offset))
            complete = max(data.rfind(b"\n"), data.rfind(b"\r")) + 1
            offset += complete
            data = data[:complete]
            if not continuing and info.st_size > min(BOOTSTRAP_READ, max_read):
                separators = [n for n in (data.find(b"\n"), data.find(b"\r")) if n >= 0]
                data = data[min(separators) + 1:] if separators else b""
            for line in data.decode("utf-8", errors="replace").replace("\r", "\n").splitlines():
                result = parse_sync_line(line, result, kind, now, context, info.st_mtime)
            return result, {"ino": info.st_ino, "dev": info.st_dev, "offset": offset,
                            "anchor": cursor_anchor(stream, offset), "result": result, "context": context}
    except (OSError, ValueError, TypeError):
        return empty_metrics(), None


def latest_opnsense_pending(path):
    """Only a bounded directory listing, never a repository tree walk."""
    path = Path(path)
    pattern = re.compile(re.escape(path.name) + r"\.tmp\.[0-9]{14}\.[0-9]+")
    latest = None
    try:
        with os.scandir(path.parent) as entries:
            for index, entry in enumerate(entries):
                if index >= 512:
                    raise OSError("pending-log directory exceeds bound")
                if pattern.fullmatch(entry.name) and (latest is None or entry.name > latest):
                    latest = entry.name
    except FileNotFoundError:
        return None
    return str(path.parent / latest) if latest else None


def repository_logs(job, previous, now, max_read):
    previous = previous if isinstance(previous, dict) else {}
    paths = {"current": job.get("log")}
    if job["kind"] in ("kali", "kali-images") and job.get("log"):
        paths["previous"] = job["log"] + ".0"
    pending_error = False
    if job["kind"] == "opnsense" and job.get("log"):
        try:
            paths["pending"] = latest_opnsense_pending(job["log"])
        except OSError:
            pending_error = True
    states, results = {}, []
    for key, path in paths.items():
        kind = "opnsense-pending" if key == "pending" else job["kind"]
        result, states[key] = sync_log(path, previous.get(key), kind, now, max_read)
        results.append(result)
    latest = max(results, key=lambda r: r["last_attempt_at"] or r["last_finished_at"] or 0).copy()
    latest["last_success_at"] = max((r["last_success_at"] for r in results if r["last_success_at"] is not None), default=None)
    if pending_error:
        latest["state"], latest["exit_code"] = "unknown", None
    return latest, states


def delivery_record(line, source, now):
    """Return only timestamp, allowlisted repository, numeric bytes."""
    if source == "http":
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError("invalid delivery row")
        rid = record.get("project")
        if rid not in REPOSITORIES:
            return None
        when, count = record.get("ts"), record.get("bytes")
        if (not timestamp(when, now) or not byte_count(count)
                or not byte_count(record.get("request_bytes"))
                or type(record.get("status")) is not int or not 100 <= record["status"] <= 599):
            raise ValueError("invalid delivery row")
        return [when, rid, count]
    if "XFER|" not in line:
        return None
    match = re.search(r"(?:^|\s)XFER\|pid=([0-9]+)\|op=(send|recv)\|size=([0-9]+)\|wire=([0-9]+)\|module=([^|]+)\|", line)
    if not match:
        raise ValueError("invalid delivery row")
    _, op, size, count, rid = match.groups()
    if rid not in REPOSITORIES or op != "send":
        return None
    count = int(count)
    if not byte_count(count) or not byte_count(int(size)):
        raise ValueError("invalid delivery row")
    # Audit retains no trustworthy raw timestamp prefix. Do NOT replay a tail:
    # new, complete serving records are assigned their local observation time.
    return [now, rid, count]


def cursor_anchor(stream, offset):
    stream.seek(max(0, offset - 128))
    return hashlib.sha256(stream.read(min(128, offset))).hexdigest()


def delivery_start(stream, info, now):
    offset = info.st_size
    stream.seek(max(0, offset - 1))
    discard = offset > 0 and stream.read(1) != b"\n"
    return {"ino": info.st_ino, "dev": info.st_dev, "offset": offset,
            "anchor": cursor_anchor(stream, offset), "discard": discard,
            "started": now, "checked": now, "events": []}


def valid_delivery_cursor(value: Any, now: float) -> TypeGuard[dict[str, Any]]:
    if not isinstance(value, dict):
        return False
    if not all(byte_count(value.get(k)) for k in ("ino", "dev", "offset")):
        return False
    if (not timestamp(value.get("started"), now) or not timestamp(value.get("checked"), now)
            or value["started"] > value["checked"] or type(value.get("discard")) is not bool
            or not isinstance(value.get("anchor"), str) or len(value["anchor"]) != 64):
        return False
    events = value.get("events")
    return isinstance(events, list) and len(events) <= 8192 and all(
        isinstance(e, list) and len(e) == 3 and timestamp(e[0], value["checked"])
        and e[1] in REPOSITORIES and byte_count(e[2]) for e in events)


def delivery_sample(path, previous, source, now, max_read):
    try:
        with open_evidence(path) as stream:
            info = os.fstat(stream.fileno())
            if (not valid_delivery_cursor(previous, now) or previous.get("ino") != info.st_ino
                    or previous.get("dev") != info.st_dev or info.st_size < previous.get("offset", 0)
                    or not 0 <= now - previous.get("checked", 0) <= 30
                    or info.st_size - previous.get("offset", 0) > max_read
                    or previous.get("anchor") != cursor_anchor(stream, previous.get("offset", 0))):
                return delivery_start(stream, info, now), None
            state = previous.copy()
            stream.seek(state["offset"])
            data = stream.read(max_read)
            complete = data.rfind(b"\n") + 1
            if len(data) - complete > 8192:
                return delivery_start(stream, info, now), None
            state["offset"] += complete
            state["anchor"] = cursor_anchor(stream, state["offset"])
            if state.get("discard") and complete:
                data = data[data.index(b"\n") + 1:complete]
                complete = len(data)
                state["discard"] = False
            events = [e for e in state["events"] if now - 60 < e[0] <= now]
            try:
                for line in data[:complete].decode("utf-8").splitlines():
                    if len(line) > 8192:
                        raise ValueError("delivery line too long")
                    event = delivery_record(line, source, now)
                    if event is not None and max(now - 60, state["started"]) < event[0] <= now:
                        events.append(event)
                coalesced = {}
                for when, rid, count in events:
                    coalesced[when, rid] = coalesced.get((when, rid), 0) + count
                events = [[when, rid, count] for (when, rid), count in sorted(coalesced.items())]
                if len(events) > 8192:
                    raise ValueError("too many delivery events")
                totals = dict.fromkeys(REPOSITORIES, 0)
                for _, rid, count in events:
                    totals[rid] += count
                    if not byte_count(totals[rid]):
                        raise ValueError("counter overflow")
            except (ValueError, UnicodeError, TypeError):
                return delivery_start(stream, info, now), None
            state["events"], state["checked"] = events, now
            return state, totals if now - state["started"] >= 60 else None
    except (OSError, ValueError, UnicodeError, TypeError):
        return None, None


def combine_transfer_rates(tcp, http):
    """Compatibility field names now cover measured HTTP plus observed rsync TCP.

    Missing coverage is not zero; independently observed windows must overlap.
    """
    unknown = dict(sync_rx_bps=None, sync_tx_bps=None, rates_observed_at=None)
    fields = tuple(unknown)
    for source in (tcp, http):
        if not isinstance(source, dict) or any(
                type(source.get(key)) not in (int, float) or not math.isfinite(source[key])
                or source[key] < 0 for key in fields):
            return unknown
    if abs(tcp['rates_observed_at'] - http['rates_observed_at']) > 2:
        return unknown
    rx, tx = (tcp[key] + http[key] for key in fields[:2])
    if not math.isfinite(rx) or not math.isfinite(tx):
        return unknown
    return dict(sync_rx_bps=rx, sync_tx_bps=tx,
                rates_observed_at=min(tcp['rates_observed_at'], http['rates_observed_at']))


def group_metrics(rows):
    result = empty_metrics()
    result["state"] = next((s for s in ("syncing", "failed", "unknown")
                            if any(r["state"] == s for r in rows)), "completed")
    for field in ("last_attempt_at", "last_finished_at"):
        result[field] = max((r[field] for r in rows if r[field] is not None), default=None)
    if all(r["last_success_at"] is not None for r in rows):
        result["last_success_at"] = min(r["last_success_at"] for r in rows)
    result["exit_code"] = next((r["exit_code"] for r in rows if r["state"] == "failed" and r["exit_code"] not in (0, None)),
                               0 if result["state"] == "completed" else None)
    result["warning"] = any(r["warning"] for r in rows)
    if all(r["size_bytes"] is not None for r in rows):
        total = sum(r["size_bytes"] for r in rows)
        if byte_count(total):
            result["size_bytes"] = total
            result["size_measured_at"] = min(r["size_measured_at"] for r in rows)
    for field in ("http_bytes_60s", "rsync_bytes_60s"):
        if all(r[field] is not None for r in rows):
            total = sum(r[field] for r in rows)
            if byte_count(total):
                result[field] = total
    result["traffic_observed_since"] = max((r["traffic_observed_since"] for r in rows
                                            if r["traffic_observed_since"] is not None), default=None)
    # Traffic is independent of upstream sync state: completed mirrors still serve downloads.
    if rows and all(r['sync_rx_bps'] is not None and r['sync_tx_bps'] is not None
                    and r['rates_observed_at'] is not None for r in rows):
        result['sync_rx_bps'] = sum(r['sync_rx_bps'] for r in rows)
        result['sync_tx_bps'] = sum(r['sync_tx_bps'] for r in rows)
        result['rates_observed_at'] = min(r['rates_observed_at'] for r in rows)
    result["repositories"] = rows
    return result


def validate_config(config):
    if (not isinstance(config, dict) or config.get("mirror_id") not in ("mirror1", "mirror2")
            or config.get("projects") != PROJECTS
            or not isinstance(config.get("repositories"), dict)
            or set(config["repositories"]) != set(REPOSITORIES)
            or not isinstance(config.get("public_host"), str)
            or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", config["public_host"])):
        raise ValueError("invalid configuration")
    def absolute(value):
        return isinstance(value, str) and os.path.isabs(value) and "\x00" not in value and ".." not in Path(value).parts
    for source in ("http_log", "rsync_log"):
        if not absolute(config.get(source)):
            raise ValueError("invalid configuration")
    for rid, job in config["repositories"].items():
        expected_kind = {"archlinux": "arch", "kali": "kali", "kali-images": "kali-images", "opnsense": "opnsense"}.get(rid, "generic")
        if (not isinstance(job, dict) or job.get("kind") != expected_kind
                or not absolute(job.get("path")) or not absolute(job.get("script"))
                or not isinstance(job.get("user"), str) or not job["user"]
                or any(job.get(k) is not None and not absolute(job[k]) for k in ("log", "trace", "lock"))):
            raise ValueError("invalid configuration")


def collect(config, state_dir, *, now=None, proc_root: str | Path = "/proc", max_read=MAX_READ):
    validate_config(config)
    sample_now = now  # None asks the TCP sampler to timestamp its actual read.
    now = time.time() if now is None else now
    if not timestamp(now, MAX_SAFE_INTEGER) or type(max_read) is not int or not 32 <= max_read <= MAX_READ:
        raise ValueError("invalid collection parameters")
    state_dir = Path(state_dir)
    private: dict[str, Any] = read_json(state_dir / "collector.json")
    if not isinstance(private, dict) or private.get("schema_version") != 1:
        private = {"schema_version": 1}
    old_success = private.get("success", {})
    if not isinstance(old_success, dict):
        old_success = {}
    sizes = read_json(state_dir / "sizes.json")
    sizes = sizes.get("repositories", {}) if isinstance(sizes, dict) and sizes.get("schema_version") == 1 else {}
    if not isinstance(sizes, dict):
        sizes = {}
    active = matching_processes(config, proc_root, now)
    seen_active = private.get("active", {})
    if not isinstance(seen_active, dict):
        seen_active = {}
    cursors = private.get("delivery", {})
    if not isinstance(cursors, dict):
        cursors = {}
    cursors = {source: cursors.get(source) for source in ("http", "rsync")}
    delivery = {}
    for source in ("http", "rsync"):
        cursors[source], delivery[source] = delivery_sample(config[source + "_log"], cursors.get(source), source, now, max_read)
    private["delivery"] = cursors
    observed_since = max((c["started"] for c in cursors.values() if c is not None), default=None)
    logs = private.get("logs", {})
    if not isinstance(logs, dict):
        logs = {}
    rows = {}
    for rid in REPOSITORIES:
        job = config["repositories"][rid]
        log_result, logs[rid] = repository_logs(job, logs.get(rid), now, max_read)
        record_path = state_dir / "runs" / rid / "state.json"
        record = read_json(record_path)
        row = observer_metrics(record, rid, now, proc_root)
        live_observer = row["state"] == "syncing"
        observer_success = row["last_success_at"] if job["kind"] != "kali" else None
        if job["kind"] == "kali":
            row = dict(log_result)
            if valid_observer(record, rid, now) and record["started_at"] > (row["last_finished_at"] or 0):
                row["state"], row["exit_code"], row["warning"] = "unknown", None, False
                row["last_attempt_at"] = max(row["last_attempt_at"] or 0, record["started_at"])
        elif not record_path.exists() or (valid_observer(record, rid, now)
                and (log_result["last_attempt_at"] or 0) > record["started_at"]):
            row = dict(log_result)
        if timestamp(observer_success, now):
            row["last_success_at"] = max(observer_success, row["last_success_at"] or 0)
        if row["state"] == "completed":
            row["last_success_at"] = row["last_finished_at"]
        candidates = [t for t in (row["last_success_at"], old_success.get(rid)) if timestamp(t, now)]
        row["last_success_at"] = max(candidates, default=None)
        previous_activity = seen_active.get(rid)
        if live_observer:
            active[rid] = min(active.get(rid, record["started_at"]), record["started_at"])
        if rid in active:
            previous_activity = active[rid]
            seen_active[rid] = previous_activity
            row["state"], row["exit_code"], row["warning"] = "syncing", None, False
            row["last_attempt_at"] = max(row["last_attempt_at"] or 0, previous_activity)
        elif (timestamp(previous_activity, now) and previous_activity > (row["last_finished_at"] or 0)
              and not (row["state"] == "failed" and (row["last_attempt_at"] or 0) >= previous_activity)):
            if row["state"] != "syncing":
                row["state"], row["exit_code"], row["warning"] = "unknown", None, False
            row["last_attempt_at"] = max(row["last_attempt_at"] or 0, previous_activity)
        size = sizes.get(rid)
        if (isinstance(size, dict) and byte_count(size.get("bytes"))
                and timestamp(size.get("measured_at"), now) and type(size.get("error")) is bool):
            row["size_bytes"] = size["bytes"]
            row["size_measured_at"] = size["measured_at"]
        for source in ("http", "rsync"):
            row[source + "_bytes_60s"] = delivery[source][rid] if delivery[source] is not None else None
        row["traffic_observed_since"] = observed_since
        rows[rid] = dict(id=rid, **row)
    diagnostics = private_reasons.Recorder(REPOSITORIES)
    sampled, private['rates'] = rates.sample(config, private.get('rates'), now=sample_now, proc_root=proc_root,
                                           diagnostic=diagnostics.sink('tcp'))
    http_sampled, private['http_rates'] = http_rates.sample(
        config, private.get('http_rates'), now=sample_now, proc_root=str(proc_root), diagnostic=diagnostics.sink('http'))
    for rid, row in rows.items():
        row.update(combine_transfer_rates(sampled.get(rid), http_sampled.get(rid)))
        for values, reason in ((sampled, private_reasons.Reason.COMBINED_TCP_NULL),
                               (http_sampled, private_reasons.Reason.COMBINED_HTTP_NULL)):
            value = values.get(rid)
            if not isinstance(value, dict) or value.get('sync_rx_bps') is None or value.get('sync_tx_bps') is None:
                diagnostics.add('combined', reason, rid)
    private["logs"] = {rid: logs.get(rid) for rid in REPOSITORIES}
    private["active"] = {rid: value for rid, value in seen_active.items() if rid in REPOSITORIES and timestamp(value, now)}
    private["success"] = {rid: row["last_success_at"] for rid, row in rows.items()}
    atomic_json(state_dir / "collector.json", private)
    try:
        atomic_json(state_dir / 'diagnostics.json', diagnostics.snapshot(now, private['rates'], private['http_rates']))
    except (OSError, ValueError, TypeError, OverflowError):
        # Sidecar is best effort and is never a source of rate validity.
        pass
    return {
        "schema_version": 1, "mirror_id": config["mirror_id"], "collected_at": now,
        "interval_seconds": 1,
        "projects": {project: group_metrics([rows[rid] for rid in ids]) for project, ids in PROJECTS.items()},
    }


TICK_TIMEOUT = 10


CollectionDeadline = private_reasons.CollectionDeadline


def expire_tick(signum, frame):
    raise CollectionDeadline()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    old_handler = signal.signal(signal.SIGALRM, expire_tick)
    old_timer = signal.setitimer(signal.ITIMER_REAL, TICK_TIMEOUT)
    try:
        config = read_json(args.config)
        validate_config(config)
        state_dir = Path(args.state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        with (state_dir / "collector.lock").open("a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            snapshot = collect(config, state_dir)
            atomic_json(args.output, snapshot, mode=0o644)
        return 0
    except (OSError, ValueError, TypeError, CollectionDeadline):
        print("telemetry collection failed", file=sys.stderr)
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        signal.signal(signal.SIGALRM, old_handler)


if __name__ == "__main__":
    raise SystemExit(main())
