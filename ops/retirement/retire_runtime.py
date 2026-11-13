#!/usr/bin/env python3
"""Constrained, local-only Keimirror telemetry retirement. See README.md."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import stat
import sys
import tempfile


PREFIX = "/opt/keimirror-telemetry/bin/"
INCLUDE = b"/etc/nginx/snippets/keimirror-telemetry-location.conf"


class Refusal(RuntimeError):
    """Safe, content-free diagnostic."""


class System:
    """Only the local OS boundary is substituted in orchestration tests."""
    UNITS = {f"keimirror-status-{name}.{suffix}" for name in ("collector", "sizes", "aggregate")
             for suffix in ("service", "timer")} | {"keimirror-rpc-helper.service"}

    def __init__(self, *, command=subprocess.run, proc=Path("/proc")):
        self.command = command
        self.proc = proc

    def owner(self):
        if os.geteuid() != 0:
            raise Refusal("root is required")
        return 0

    def identity(self):
        return {"hostname": socket.gethostname(),
                "boot_id": (self.proc / "sys/kernel/random/boot_id").read_text().strip()}

    def execute(self, args, *, content=None, capture=False):
        result = self.command(args, input=content, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=60,
                              env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        if result.returncode:
            raise Refusal("local command failed; output suppressed")
        return result.stdout if capture else b""

    def properties(self, unit, properties):
        data = self.execute(["/usr/bin/systemctl", "show", "--property=" + properties, unit], capture=True)
        return dict(line.split("=", 1) for line in data.decode("ascii").splitlines() if "=" in line)

    def unit_state(self, unit):
        if unit not in self.UNITS:
            raise Refusal("unit not allowlisted")
        values = self.properties(unit, "ActiveState,UnitFileState")
        return {"active": values["ActiveState"], "enabled": values["UnitFileState"]}

    def pid(self, unit):
        values = self.properties(unit, "ActiveState,MainPID")
        pid = int(values["MainPID"])
        if values["ActiveState"] != "active" or pid <= 1:
            raise Refusal("native service is not active")
        return pid

    def nginx_pid(self):
        return self.pid("nginx.service")

    def rsync_pid(self):
        return self.pid("rsync.service")

    def action(self, verb, unit=None):
        if verb in ("stop", "start", "enable", "disable") and unit in self.UNITS:
            self.execute(["/usr/bin/systemctl", verb, unit])
        elif unit is None and verb == "daemon-reload":
            self.execute(["/usr/bin/systemctl", "daemon-reload"])
        elif unit is None and verb == "nginx-test":
            self.execute(["/usr/sbin/nginx", "-t"])
        elif unit is None and verb == "nginx-reload":
            self.execute(["/usr/bin/systemctl", "reload", "nginx.service"])
        else:
            raise Refusal("system mutation is not allowlisted")

    def check_shell(self, content):
        shebang = content.split(b"\n", 1)[0]
        shells = {b"#!/bin/bash": "/bin/bash", b"#!/usr/bin/bash": "/bin/bash",
                  b"#!/usr/bin/env bash": "/bin/bash", b"#!/bin/sh": "/bin/sh",
                  b"#!/usr/bin/sh": "/bin/sh", b"#!/usr/bin/env sh": "/bin/sh"}
        if shebang not in shells:
            raise Refusal("unsupported script interpreter")
        self.execute([shells[shebang], "-n"], content=content)

    def observer_count(self):
        count = 0
        for process in self.proc.iterdir():
            if not process.name.isdecimal():
                continue
            try:
                args = (process / "cmdline").read_bytes().split(b"\0")
            except FileNotFoundError:
                continue  # A process exited during this read-only census.
            if b"/opt/keimirror-telemetry/observe.py" in args:
                count += 1
        return count


def digest(data):
    return hashlib.sha256(data).hexdigest()


SCRIPTS = {f"/home/me0kmin27/script/{name}.sh" for name in (
    "arch-ssl", "artix", "artix-iso", "artix-weekly-iso", "cachyos", "endeavouros",
    "fyralabs", "gentoo", "gentoo-portage", "manjaro", "opnsense", "proxmox")}
SCRIPTS.add("/home/archvsync/bin/mirror-kali-images")
FRAGMENTS = {"/etc/nginx/conf.d/keimirror-live.conf", "/etc/nginx/conf.d/keimirror-telemetry.conf",
             "/etc/nginx/modules-enabled/90-keimirror-live.conf", INCLUDE.decode(),
             "/usr/lib/systemd/system/nginx.service.d/keimirror-live.conf"}


def no_symlink_parents(path):
    if any(p.is_symlink() for p in path.parents):
        raise Refusal("symlink parent is not supported")


def snapshot(path, logical):
    no_symlink_parents(path)
    metadata = path.lstat()
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)) or metadata.st_nlink != 1:
        raise Refusal("regular file or single symlink required")
    kind = "symlink" if stat.S_ISLNK(metadata.st_mode) else "file"
    if kind == "symlink":
        data = os.fsencode(os.readlink(path))
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if os.fstat(stream.fileno()) != metadata:
                raise Refusal("file changed before read")
            data = stream.read()
            latest = os.fstat(stream.fileno())
            if (latest.st_size, latest.st_mtime_ns, latest.st_ctime_ns) != (
                    metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns):
                raise Refusal("file changed during read")
    result = {"path": logical, "kind": kind, "sha256": digest(data),
              "uid": metadata.st_uid, "gid": metadata.st_gid,
              "mode": stat.S_IMODE(metadata.st_mode)}
    if kind == "symlink":
        result["target"] = os.fsdecode(data)
    return result, data


def check(path, expected):
    if expected["kind"] == "absent":
        if os.path.lexists(path):
            raise Refusal("unexpected existing path")
        return
    actual, _ = snapshot(path, expected["path"])
    if actual != {k: v for k, v in expected.items() if k != "operation"}:
        raise Refusal("file content or metadata drift")


def atomic(path, data, metadata):
    """Same-directory replacement; no shell or interpretation of file bytes."""
    fd, temporary = tempfile.mkstemp(prefix=".keimirror-retire-", dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            if metadata["kind"] == "file":
                stream.write(data)
                stream.flush()
                os.fchown(stream.fileno(), metadata["uid"], metadata["gid"])
                os.fchmod(stream.fileno(), metadata["mode"])
                os.fsync(stream.fileno())
        if metadata["kind"] == "symlink":
            tmp.unlink()
            tmp.symlink_to(os.fsdecode(data))
            os.chown(tmp, metadata["uid"], metadata["gid"], follow_symlinks=False)
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        if os.path.lexists(tmp):
            tmp.unlink()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_json(path, value, owner):
    atomic(path, json.dumps(value, sort_keys=True).encode(),
           {"kind": "file", "uid": owner, "gid": os.getgid(), "mode": 0o600})


def native_script(path, data):
    """Inverse of the reviewed historical adapter's four exact forms."""
    prefix = PREFIX.encode()
    if path.endswith("/arch-ssl.sh"):
        substitutions = [(b"cmd=(" + prefix + command + b")", b"cmd=(" + command + b")")
                         for command in (b"rsync-ssl --type=openssl", b"rsync")]
    elif path.endswith("/opnsense.sh"):
        fallback = b'[[ -z "${RSYNC_BIN}" ]] && RSYNC_BIN="rsync"'
        if data.count(fallback) != 1:
            raise Refusal("unexpected OPNsense fallback")
        substitutions = [(b'RSYNC_BIN="' + prefix + b'rsync"',
                          b'RSYNC_BIN="$(command -v rsync || true)"')]
    elif path.endswith("/mirror-kali-images"):
        command = b'rsync $RSYNC_OPTIONS $RSYNC_URL $TO >"$LOG_FILE" 2>"$ERROR_FILE"'
        substitutions = [(prefix + command, command)]
    else:
        pattern = rb'(?m)^[ \t]*RSYNC="' + re.escape(prefix) + rb'(rsync(?:-ssl)?)"[ \t]*\r?$'
        matches = re.findall(pattern, data)
        if len(matches) != 1:
            raise Refusal("unexpected generic script marker")
        substitutions = [(prefix + matches[0], b"/usr/bin/" + matches[0])]
    for old, new in substitutions:
        if data.count(old) != 1:
            raise Refusal("missing or duplicate native script marker")
        data = data.replace(old, new)
    if prefix in data:
        raise Refusal("unhandled wrapper reference")
    return data


def transformed(record, data):
    after = {k: v for k, v in record.items() if k != "operation"}
    operation = record["operation"]
    if operation == "script":
        data = native_script(record["path"], data)
    elif operation == "native-link":
        data = ("/usr/bin/" + Path(record["path"]).name).encode()
        after.update(kind="symlink", target=data.decode(), mode=0o777)
    elif operation == "include":
        pattern = rb"(?m)^[ \t]*include " + re.escape(INCLUDE) + rb";[ \t]*\r?\n"
        if len(re.findall(pattern, data)) != 1 or data.count(INCLUDE) != 1:
            raise Refusal("expected one standalone telemetry include")
        data = re.sub(pattern, b"", data)
    elif operation == "remove":
        return {"path": record["path"], "kind": "absent"}, b""
    else:
        raise Refusal("unsupported operation")
    after["sha256"] = digest(data)
    return after, data


def validate_plan(plan, local, system):
    role = plan["role"]
    hosts = {"homepage": "keiminem", "mirror1": "mirror", "mirror2": "mirror2"}
    if plan["version"] != 1 or role not in hosts or plan["hostname"] != hosts[role]:
        raise Refusal("unsupported host role or plan version")
    if system.identity() != {k: plan[k] for k in ("hostname", "boot_id")}:
        raise Refusal("host identity or boot drift")
    site = "/etc/nginx/sites-available/" + ("keiverse" if role == "homepage" else "theme")
    expected = {site: "include", INCLUDE.decode(): "remove"}
    units = {"keimirror-status-aggregate." + suffix for suffix in ("service", "timer")}
    if role != "homepage":
        expected.update({p: "script" for p in SCRIPTS})
        expected.update({PREFIX + name: "native-link" for name in ("rsync", "rsync-ssl")})
        expected.update({p: "remove" for p in FRAGMENTS})
        units = {f"keimirror-status-{name}.{suffix}" for name in ("collector", "sizes")
                 for suffix in ("service", "timer")}
        if role == "mirror1":
            units.add("keimirror-rpc-helper.service")
    if ({r["path"]: r["operation"] for r in plan["changes"]} != expected
            or len(plan["changes"]) != len(expected) or set(plan["units"]) != units):
        raise Refusal("plan does not match the exact retirement allowlist")
    for state in plan["units"].values():
        if (not isinstance(state, dict) or set(state) != {"active", "enabled"}
                or state["active"] not in ("active", "inactive", "failed")
                or state["enabled"] not in ("enabled", "disabled", "static")):
            raise Refusal("unsupported unit state")
    records = plan["changes"] + plan["protected"]
    if len({r["path"] for r in records}) != len(records):
        raise Refusal("duplicate or overlapping paths")
    for record in records:
        path = record["path"]
        if (not isinstance(path, str) or not path.startswith("/") or str(Path(path)) != path
                or ".." in Path(path).parts or any(ord(c) < 32 for c in path)
                or record["kind"] not in ("file", "symlink")
                or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
                or any(type(record[k]) is not int or record[k] < 0 for k in ("uid", "gid", "mode"))):
            raise Refusal("invalid path or expected metadata")
        no_symlink_parents(local(path))
    if any(r["kind"] != "file" for r in plan["changes"]):
        raise Refusal("changed paths must be explicit canonical regular files")
    protected = {r["path"]: r for r in plan["protected"]}
    required = {"/usr/sbin/nginx", "/etc/nginx/nginx.conf", "/etc/crontab",
                site.replace("sites-available", "sites-enabled")}
    if role != "homepage":
        required.update({"/usr/bin/rsync", "/usr/bin/rsync-ssl", "/etc/rsyncd.conf",
                         "/home/archvsync/bin/ftpsync", "/opt/keimirror-telemetry/observe.py"})
    if not required.issubset(protected) or not any(p.startswith("/etc/ssh/") for p in protected):
        raise Refusal("missing protected binary, native script, SSH or cron inventory")
    alias = site.replace("sites-available", "sites-enabled")
    link = protected[alias]
    if link["kind"] != "symlink" or os.path.normpath(os.path.join(str(Path(alias).parent), link["target"])) != site:
        raise Refusal("site alias does not name canonical target")
    directory = plan["backup_dir"]
    if not re.fullmatch(r"/var/backups/keimirror-retirement/[a-zA-Z0-9_-]+", directory):
        raise Refusal("backup must use task-owned directory outside webroots")
    no_symlink_parents(local(directory))
    parent = local(directory).parent.stat()
    if parent.st_uid != system.owner() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise Refusal("backup parent must be root-owned mode 0700")


def load_private(path, owner):
    metadata, data = snapshot(path, str(path))
    if metadata["kind"] != "file" or metadata["uid"] != owner or metadata["mode"] != 0o600:
        raise Refusal("plan or journal must be root-owned mode 0600")
    return data


def run(plan_path, mode, *, system, root=Path("/")):
    plan_bytes = load_private(Path(plan_path), system.owner())
    plan = json.loads(plan_bytes)
    local = lambda path: root / path.lstrip("/")
    validate_plan(plan, local, system)
    backup = local(plan["backup_dir"])
    if mode in ("verify", "rollback"):
        metadata = backup.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != system.owner() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise Refusal("invalid private backup directory")
        journal = json.loads(load_private(backup / "journal.json", system.owner()))
        if journal["plan_sha256"] != digest(plan_bytes) or journal["units"] != plan["units"]:
            raise Refusal("journal does not match private plan")
        if len(journal["files"]) != len(plan["changes"]):
            raise Refusal("incomplete journal")
        for i, (item, record) in enumerate(zip(journal["files"], plan["changes"])):
            if item["before"] != {k:v for k,v in record.items() if k != "operation"} or item["backup"] != str(i):
                raise Refusal("journal file inventory mismatch")
        validated_backups(plan, journal, local)
        if mode == "rollback":
            return rollback(plan, journal, local, system)
        return verify(plan, journal, local, system)
    if os.path.lexists(backup):
        raise Refusal("preexisting backup directory")
    units = {u: system.unit_state(u) for u in plan["units"]}
    if units != plan["units"]:
        raise Refusal("unit state drift")
    for record in plan["changes"] + plan["protected"]:
        check(local(record["path"]), record)
    prepared = []
    for record in plan["changes"]:
        before, data = snapshot(local(record["path"]), record["path"])
        after, output = transformed(record, data)
        if record["operation"] == "script":
            system.check_shell(output)
        prepared.append((before, data, after, output))
    units = {u: system.unit_state(u) for u in plan["units"]}
    if units != plan["units"]:
        raise Refusal("unit state drift during preparation")
    journal = {"plan_sha256": digest(plan_bytes), "units": units,
               "rsync_master_pid": system.rsync_pid() if plan["role"] != "homepage" else None,
               "nginx_master_pid": system.nginx_pid(), "files": []}
    if mode == "check":
        return {"status": "ready", "changed_files": len(prepared), "plan_sha256": digest(plan_bytes)}
    if mode != "apply":
        raise Refusal("unsupported mode")
    backup.mkdir(mode=0o700)
    for i, (before, data, after, _) in enumerate(prepared):
        atomic(backup / str(i), data, before)
        journal["files"].append({"before": before, "after": after, "backup": str(i)})
    private_json(backup / "journal.json", journal, system.owner())
    # A durable journal precedes every system mutation. Retain it on failure.
    journal["reload_attempted"] = False
    private_json(backup / "journal.json", journal, system.owner())
    try:
        # Stop every timer before any service; never touch observer processes.
        for unit in sorted(units, key=lambda u: (not u.endswith(".timer"), u)):
            system.action("stop", unit)
            if units[unit]["enabled"] == "enabled":
                system.action("disable", unit)
        operations = {r["path"]: r["operation"] for r in plan["changes"]}
        order = {"script": 0, "native-link": 1, "include": 2, "remove": 3}
        for before, _, after, output in sorted(prepared, key=lambda c: order[operations[c[0]["path"]]]):
            target = local(before["path"])
            check(target, before)
            if after["kind"] == "absent":
                target.unlink()
                sync_dir(target.parent)
            else:
                atomic(target, output, after)
            check(target, after)
        system.action("daemon-reload")
        system.action("nginx-test")
        journal["reload_attempted"] = True
        private_json(backup / "journal.json", journal, system.owner())
        system.action("nginx-reload")
        result = verify(plan, journal, local, system)
        private_json(backup / "receipt.json", result, system.owner())
        return result
    except BaseException:
        try:
            rollback(plan, journal, local, system)
        except BaseException:
            raise Refusal("retirement failed; recovery incomplete; inspect private backups") from None
        raise Refusal("retirement failed; original files and prior active units restored") from None


def validated_backups(plan, journal, local):
    backup = local(plan["backup_dir"])
    outputs = []
    for item in journal["files"]:
        check(backup / item["backup"], item["before"])
        _, data = snapshot(backup / item["backup"], item["before"]["path"])
        outputs.append((item, data))
    return outputs


def rollback(plan, journal, local, system):
    originals = validated_backups(plan, journal, local)
    # A rollback is not authorization to overwrite a concurrent operator edit.
    for item, _ in originals:
        path = local(item["before"]["path"])
        try:
            check(path, item["after"])
        except (Refusal, FileNotFoundError):
            check(path, item["before"])
    for record in plan["protected"]:
        check(local(record["path"]), record)
    backup = local(plan["backup_dir"])
    receipt = backup / "receipt.json"
    if receipt.exists():
        receipt.unlink()
    failures = []

    def attempt(label, function, *args):
        try:
            function(*args)
            return True
        except BaseException:
            failures.append(label)
            return False

    for item, data in reversed(originals):
        before = item["before"]
        attempt("restore-file", atomic, local(before["path"]), data, before)
        attempt("restore-readback", check, local(before["path"]), before)
    attempt("daemon-reload", system.action, "daemon-reload")
    syntax_ok = attempt("nginx-test", system.action, "nginx-test")
    if syntax_ok and journal.get("reload_attempted", True):
        attempt("nginx-reload", system.action, "nginx-reload")
    for unit, state in journal["units"].items():
        if state["enabled"] in ("enabled", "disabled"):
            attempt("enablement:" + unit, system.action,
                    "enable" if state["enabled"] == "enabled" else "disable", unit)
    for unit in sorted(journal["units"], key=lambda u: (u.endswith(".timer"), u)):
        if journal["units"][unit]["active"] == "active":
            attempt("start:" + unit, system.action, "start", unit)
    for record in plan["protected"]:
        attempt("protected-readback", check, local(record["path"]), record)
    for unit, previous in journal["units"].items():
        try:
            current = system.unit_state(unit)
            if (current["enabled"] != previous["enabled"]
                    or (previous["active"] == "active" and current["active"] != "active")
                    or (previous["active"] != "active" and current["active"] not in ("inactive", "failed"))):
                failures.append("unit-readback:" + unit)
        except BaseException:
            failures.append("unit-readback:" + unit)
    try:
        if system.nginx_pid() != journal["nginx_master_pid"]:
            failures.append("nginx-pid-readback")
        if plan["role"] != "homepage" and system.rsync_pid() != journal["rsync_master_pid"]:
            failures.append("rsync-pid-readback")
    except BaseException:
        failures.append("native-pid-readback")
    if failures:
        raise Refusal("recovery incomplete: " + ", ".join(failures))
    return {"status": "rolled-back"}


def verify(plan, journal, local, system):
    for item in journal["files"]:
        check(local(item["after"]["path"]), item["after"])
    for record in plan["protected"]:
        check(local(record["path"]), record)
    for unit in journal["units"]:
        state = system.unit_state(unit)
        if state["active"] not in ("inactive", "failed") or state["enabled"] not in ("disabled", "static"):
            raise Refusal("telemetry unit remains enabled or active")
    pid = system.nginx_pid()
    if pid != journal["nginx_master_pid"]:
        raise Refusal("nginx master PID changed")
    if plan["role"] != "homepage" and system.rsync_pid() != journal["rsync_master_pid"]:
        raise Refusal("native rsync daemon PID changed")
    observers = system.observer_count()
    return {"status": "retired", "nginx_master_pid": pid, "observer_count": observers,
            "rsync_master_pid": journal["rsync_master_pid"],
            "code_cleanup": "pending-observer-drain" if observers else "retained-needs-separate-review"}


def main(argv=None, *, system=None, root=Path("/")):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", default="check", choices=("check", "apply", "verify", "rollback"))
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(args.plan, args.mode, system=system or System(), root=root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Refusal as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("retirement refused; private input or local command failed; no contents logged", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
