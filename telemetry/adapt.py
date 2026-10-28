"""Fail-closed, binary-path-only adapters for the inventoried sync scripts."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import overload

BIN = "/opt/keimirror-telemetry/bin"


class RollbackError(OSError):
    """Contains only failed target paths, never source or provider error text."""


@overload
def adapt_source(kind: str, source: bytes) -> bytes: ...


@overload
def adapt_source(kind: str, source: str) -> str: ...


def adapt_source(kind, source):
    """Return a byte-preserving source of the same type; never execute it."""
    if isinstance(source, bytes):
        return adapt_source(kind, source.decode("latin1")).encode("latin1")
    if kind == "arch":
        pattern = re.compile(r'(?m)^([ \t]*cmd=\()(?:(' + re.escape(BIN) + r')/)?'
                             r'(rsync-ssl --type=openssl|rsync)(\)[ \t]*\r?$)')
        matches = list(pattern.finditer(source))
        if (len(matches) != 2 or len(re.findall(r'\bcmd\s*=', source)) != 2
                or {m[3] for m in matches} != {"rsync", "rsync-ssl --type=openssl"}
                or len({m[2] for m in matches}) != 1):
            raise ValueError("unsupported or ambiguous arch markers")
        return pattern.sub(lambda m: m[1] + BIN + "/" + m[3] + m[4], source)
    if kind == "opnsense":
        old = 'RSYNC_BIN="$(command -v rsync || true)"'
        new = f'RSYNC_BIN="{BIN}/rsync"'
        fallback = '[[ -z "${RSYNC_BIN}" ]] && RSYNC_BIN="rsync"'
        pattern = re.compile(r'(?m)^([ \t]*)(?:' + re.escape(old) + "|" +
                             re.escape(new) + r')([ \t]*\r?$)')
        if (len(pattern.findall(source)) != 1
                or len(re.findall(r'\bRSYNC_BIN\s*=', source)) != 2
                or len(re.findall(r'(?m)^[ \t]*' + re.escape(fallback) + r'[ \t]*\r?$', source)) != 1):
            raise ValueError("unsupported or ambiguous opnsense markers")
        return pattern.sub(lambda m: m[1] + new + m[2], source)
    if kind == "kali":
        # Native ftpsync two-stage logs/trace are authoritative; never wrap it.
        return source
    if kind == "kali-images":
        pattern = re.compile(r'(?m)^([ \t]+)(?:rsync|' + re.escape(BIN) + r'/rsync)'
                             r'( \$RSYNC_OPTIONS \$RSYNC_URL \$TO >"\$LOG_FILE" 2>"\$ERROR_FILE"[ \t]*\r?$)')
        commands = re.findall(r'(?m)^[ \t]*(?:rsync|' + re.escape(BIN) + r'/rsync)(?=[ \t])', source)
        if len(pattern.findall(source)) != 1 or len(commands) != 1:
            raise ValueError("unsupported or ambiguous kali-images marker")
        return pattern.sub(lambda m: m[1] + BIN + "/rsync" + m[2], source)
    if kind != "generic":
        raise ValueError("unsupported adapter kind")
    pattern = re.compile(r'(?m)^([ \t]*RSYNC=")(/usr/bin|' + re.escape(BIN) +
                         r')/(rsync(?:-ssl)?)("[ \t]*\r?$)')
    if len(pattern.findall(source)) != 1 or len(re.findall(r'\bRSYNC\s*=', source)) != 1:
        raise ValueError("unsupported or ambiguous generic marker")
    return pattern.sub(lambda match: match[1] + BIN + "/" + match[3] + match[4], source)


@dataclass
class Change:
    path: Path
    kind: str
    before: bytes
    after: bytes
    metadata: os.stat_result

    def audit(self):
        return {"path": str(self.path), "kind": self.kind,
                "before_sha256": hashlib.sha256(self.before).hexdigest(),
                "after_sha256": hashlib.sha256(self.after).hexdigest(),
                "changed": self.before != self.after}


def check_shell(source):
    """Parse stdin with -n in a clean environment; never source or execute a job."""
    shebang = source.split(b"\n", 1)[0]
    shells = {b"#!/bin/bash": "/bin/bash", b"#!/usr/bin/bash": "/bin/bash",
              b"#!/usr/bin/env bash": "/bin/bash", b"#!/bin/sh": "/bin/sh",
              b"#!/usr/bin/sh": "/bin/sh", b"#!/usr/bin/env sh": "/bin/sh"}
    if shebang not in shells:
        raise ValueError("unsupported shell shebang")
    result = subprocess.run([shells[shebang], "-n"], input=source,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, timeout=15)
    if result.returncode:
        raise ValueError("shell syntax check failed")


def _identity(metadata):
    # atime can legitimately change when the file is read for validation.
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid,
            metadata.st_gid, metadata.st_nlink, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def read_baseline(path):
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("script path must be absolute and normalized")
    if any(p.is_symlink() for p in path.parents):
        raise ValueError("script path has a symlink component")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("script must be a regular file with no hardlinks")
        before = stream.read()
        if _identity(metadata) != _identity(os.fstat(stream.fileno())):
            raise ValueError("script changed while reading")
    return before, metadata


def prepare(config, expected_hashes=None):
    if (not isinstance(config, dict) or not isinstance(config.get("repositories"), dict)
            or not config["repositories"]):
        raise ValueError("configuration must contain nonempty repositories")
    for repo in config["repositories"].values():
        if (not isinstance(repo, dict)
                or any(not isinstance(repo.get(key), str) or not repo[key] for key in ("script", "kind", "path"))):
            raise ValueError("invalid repository configuration")
        for key in ("script", "path"):
            value = repo[key]
            if (not Path(value).is_absolute() or str(Path(value)) != value or ".." in Path(value).parts
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise ValueError("configured paths must be literal absolute normalized paths")
    changes = []
    seen = set()
    for repo in config["repositories"].values():
        path = Path(repo["script"])
        if path in seen:
            raise ValueError("duplicate script path")
        seen.add(path)
        before, metadata = read_baseline(path)
        after = adapt_source(repo["kind"], before)
        check_shell(after)
        changes.append(Change(path, repo["kind"], before, after, metadata))
    if expected_hashes is not None:
        validate_expected_hashes(changes, expected_hashes)
    return changes


def validate_expected_hashes(changes, expected_hashes):
    if (not isinstance(expected_hashes, dict)
            or set(expected_hashes) != {str(c.path) for c in changes}
            or any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
                   for h in expected_hashes.values())):
        raise ValueError("expected hashes must cover exactly all configured script paths")
    for change in changes:
        if hashlib.sha256(change.before).hexdigest() != expected_hashes[str(change.path)]:
            raise ValueError("baseline hash mismatch")


def backup_path(directory, change):
    return directory / (hashlib.sha256(os.fsencode(change.path)).hexdigest() + ".original")


def validate_backup_directory(directory, config):
    if not directory.is_absolute() or ".." in directory.parts:
        raise ValueError("backup directory must be an absolute normalized path")
    for ancestor in (directory, *directory.parents):
        if ancestor.is_symlink():
            raise ValueError("backup directory has a symlink component")
    roots = {Path("/srv/mirrors"), Path("/srv/data"), Path("/srv/keiverse")}
    roots.update(Path(repo["path"]).parent for repo in config["repositories"].values())
    if any(directory.is_relative_to(root) or directory.resolve().is_relative_to(root.resolve())
           for root in roots):
        raise ValueError("backup directory must be outside all webroots")
    if directory.exists():
        metadata = directory.stat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700):
            raise ValueError("backup directory must be owned by this user with mode 0700")


def _atomic_replace(path, content, metadata):
    """Publish a same-directory temporary inode with the original uid/gid/mode."""
    fd, temporary = tempfile.mkstemp(prefix=".keimirror-adapt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _assert_unchanged(change):
    content, metadata = read_baseline(change.path)
    if content != change.before or _identity(metadata) != _identity(change.metadata):
        raise ValueError("baseline changed after preflight")


def check_backups(changes, directory):
    for change in changes:
        backup = backup_path(directory, change)
        if backup.exists() or backup.is_symlink():
            original, metadata = read_baseline(backup)
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
                raise ValueError("existing backup is not owner-only")
            if (adapt_source(change.kind, original) != change.after
                    or (change.before != change.after and original != change.before)):
                raise ValueError("existing backup does not match this script")


def _verify_readback(change, expected):
    content, metadata = read_baseline(change.path)
    digest = hashlib.sha256(content).hexdigest()
    if (digest != hashlib.sha256(expected).hexdigest()
            or (metadata.st_uid, metadata.st_gid, metadata.st_mode)
            != (change.metadata.st_uid, change.metadata.st_gid, change.metadata.st_mode)):
        raise ValueError("readback content or metadata verification failed")
    return digest


def apply_changes(changes, directory, expected_hashes=None):
    validate_expected_hashes(changes, expected_hashes)
    for change in changes:
        _assert_unchanged(change)
    check_backups(changes, directory)
    changed = [c for c in changes if c.before != c.after]
    if changed:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for change in changed:
        backup = backup_path(directory, change)
        if not backup.exists():
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(change.before)
                stream.flush()
                os.fsync(stream.fileno())
    attempted = []
    try:
        for change in changed:
            _assert_unchanged(change)
            # Track before replacement: rename may succeed and a later fsync may fail.
            attempted.append(change)
            _atomic_replace(change.path, change.after, change.metadata)
        hashes = {}
        for change in changes:
            hashes[str(change.path)] = _verify_readback(change, change.after)
        return hashes
    except BaseException:
        failures = []
        for change in reversed(attempted):
            try:
                _atomic_replace(change.path, change.before, change.metadata)
                _verify_readback(change, change.before)
            except BaseException:
                failures.append(str(change.path))
        if failures:
            raise RollbackError("rollback incomplete; restore private backups for: " + ", ".join(failures)) from None
        raise


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--apply", action="store_true", help="apply after all preflight checks (default: dry-run)")
    parser.add_argument("--expected-hashes", help="JSON object mapping every absolute script path to its SHA-256")
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.config).read_text(), object_pairs_hook=_json_object)
        expected = None
        if args.expected_hashes:
            expected = json.loads(Path(args.expected_hashes).read_text(), object_pairs_hook=_json_object)
            if not isinstance(expected, dict):
                raise ValueError("expected hashes must be an object")
        changes = prepare(config, expected)
        directory = Path(args.backup_dir)
        validate_backup_directory(directory, config)
        check_backups(changes, directory)
        hashes = apply_changes(changes, directory, expected) if args.apply else {}
        records = []
        for change in changes:
            record = change.audit()
            record["backup"] = str(backup_path(directory, change))
            if args.apply:
                record["readback_sha256"] = hashes[str(change.path)]
            records.append(record)
        print(json.dumps({"applied": args.apply, "scripts": records}, sort_keys=True))
        return 0
    except RollbackError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("adapter failed; no script contents logged", file=sys.stderr)
        return 1


@dataclass
class _Directive:
    words: list
    scope: tuple
    start: int
    end: int
    block: bool


def _nginx_tokens(source):
    """Small structural lexer, not an nginx syntax validator or include resolver."""
    i = 0
    while i < len(source):
        char = source[i]
        if char in " \t\r\n":
            i += 1
            continue
        if char == "#":
            end = source.find("\n", i)
            i = len(source) if end == -1 else end + 1
            continue
        start = i
        if char in ";{}":
            i += 1
            yield char, start, i, True
            continue
        if char in "\"'":
            quote = char
            i += 1
            text_start = i
            while i < len(source) and source[i] != quote:
                i += 2 if source[i] == "\\" else 1
            if i >= len(source):
                raise ValueError("unterminated nginx string")
            word = source[text_start:i]
            i += 1
            if i < len(source) and source[i] not in " \t\r\n;{})":
                raise ValueError("unsupported nginx token")
        else:
            while i < len(source) and source[i] not in " \t\r\n;{}":
                if source[i] in "\"'":
                    raise ValueError("unsupported nginx token")
                i += 2 if source[i] == "\\" else 1
            word = source[start:i]
        yield word, start, i, False


def _nginx_directives(source):
    directives, scope, pending = [], [], []
    start = 0
    for word, token_start, end, control in _nginx_tokens(source):
        if control and word == "}":
            if pending or not scope:
                raise ValueError("unbalanced nginx scope")
            scope.pop()
        elif control:
            if not pending:
                raise ValueError("empty nginx directive")
            directives.append(_Directive(pending, tuple(scope), start, end, word == "{"))
            if word == "{":
                scope.append(len(directives) - 1)
            pending = []
        else:
            if not pending:
                start = token_start
            pending.append(word)
    if scope or pending:
        raise ValueError("incomplete nginx configuration")
    return directives


def add_location_include(source, expected_server, expected_root, include_path):
    """Insert after one verified server root line; otherwise refuse without edits.

    This pure transform does not follow existing includes. The caller must verify
    the latest full config and run nginx -t before any deployment/reload.
    """
    if isinstance(source, bytes):
        return add_location_include(source.decode("latin1"), expected_server,
                                    expected_root, include_path).encode("latin1")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", expected_server):
        raise ValueError("invalid expected server name")
    for path in (expected_root, include_path):
        if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", path)
                or any(part in (".", "..", "") for part in path.split("/")[1:])):
            raise ValueError("expected root/include must be literal absolute paths")
    directives = _nginx_directives(source)
    servers = [(i, d) for i, d in enumerate(directives) if d.words[0] == "server"]
    if (len(servers) != 1 or servers[0][1].words != ["server"]
            or not servers[0][1].block or servers[0][1].scope
            or any(not d.scope and d is not servers[0][1] for d in directives)):
        raise ValueError("expected exactly one top-level server block")
    scope = (servers[0][0],)
    names = [d for d in directives if d.scope == scope and d.words[0] == "server_name"]
    roots = [d for d in directives if d.scope == scope and d.words[0] == "root"]
    if (len(names) != 1 or names[0].words != ["server_name", expected_server] or names[0].block
            or len(roots) != 1 or roots[0].words != ["root", expected_root] or roots[0].block):
        raise ValueError("server name/root do not exactly match expected scope")
    root = roots[0]
    line_start = source.rfind("\n", 0, root.start) + 1
    line_end = source.find("\n", root.end)
    indent = source[line_start:root.start]
    if (line_end == -1 or not re.fullmatch(r"[ \t]*", indent)
            or not re.fullmatch(r"[ \t]*(?:#[^\r\n]*)?\r?", source[root.end:line_end])):
        raise ValueError("root must occupy a complete standalone line")
    endpoints = {"/.well-known/keimirror-status.json", "/status/mirrors.json"}
    if any(d.words[0] == "location" and endpoints.intersection(d.words[1:]) for d in directives):
        raise ValueError("status location already exists")
    existing = [d for d in directives if d.words[0] == "include" and include_path in d.words[1:]]
    if existing:
        if (len(existing) != 1 or existing[0].scope != scope
                or existing[0].words != ["include", include_path] or existing[0].block):
            raise ValueError("include is duplicated or in the wrong scope")
        return source
    newline = "\r\n" if source[line_end - 1] == "\r" else "\n"
    insertion = indent + f"include {include_path};" + newline
    return source[:line_end + 1] + insertion + source[line_end + 1:]


if __name__ == "__main__":
    raise SystemExit(main())
