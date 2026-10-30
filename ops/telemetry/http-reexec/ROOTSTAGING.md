# ROOTSTAGING — proposed replacement, NOT executed or approved

The previous pending operator-checkout command is superseded because the
independent review found an actual pre-validation root-execution defect. Its
180-second executor deadline is also insufficient. This is a new logic-change
candidate, not an approval bypass or a replay of that held command.

Parent delta review and the normal tool approval gate must approve each exact
command below before execution. Do not wrap commands in a shell `-c`, remove
flags, move code to conceal it, or infer approval from source CI. The Python
`-c` bootstrap is explicit and must pass the normal script-execution approval.
No privileged staging, harness runtime, production mutation, or merge has run.

## Trust and closure

The literal bootstrap below uses only host Python stdlib, captures the script
via no-follow directory descriptors, requires bounded single-link regular bytes,
and checks its independently reviewed SHA256 BEFORE compile/exec. It does not
execute a user-supplied bootstrap file. The same bootstrap verifies root-stage
script bytes for each runtime command. Environment is cleared before Python;
`-I -S -B` excludes user path/site and bytecode writes. Trusted host OS Python,
stdlib, system executables and loader libraries remain prerequisites; no
captured artifact library, Python cache or arbitrary reader tree is copied.

The separately approved manifest SHA binds the full local root-code closure:
root_stage.py, isolated.py, transaction.py, inventory.py, both host inventories,
and both approved reader modules. Every copied byte is checked, in a newly
created root:root 0700 stage beneath trusted /var/lib. Symlink ancestors,
nonregular inputs, hardlinks and oversized inputs fail closed. Only explicit
pinned files are copied to fresh inodes. Both reader modules come from the same
approved repository sources, retained byte-for-byte in this directory's reader/
snapshot from candidate 3b600a42030a3a8090ef944c2d565210fbcc871c. These
reviewed pins do not follow unrelated telemetry changes on the PR merge base.
An artifact-side manifest cannot authorize itself.

Runtime verifies the entire root source and artifact closure before importing
isolated; the harness verifies its fresh private artifact copies before loading
transaction source or issuing even cleanup commands. Captured transaction
source is compiled directly, not retrieved from sys.modules or bytecode cache.
The transaction implementation and its 270/180/450-second monotonic budget are
unchanged. Each external executor timeout is **660 seconds**, including margin
for staging/preparation/cleanup; the transaction's absolute ceiling stays 450.
This is not a realtime bound on kernel-uninterruptible I/O.

The target below is proposed, not asserted to exist. After approved staging,
root_stage.verify checks actual owner/group/mode/ancestor and digest metadata;
forward/rollback independently repeat that verification. No existing stage is
reused or repaired. Outputs are private target/forward.json and rollback.json.

Root manifest SHA256: `11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415`

Stager SHA256: `ef37b412d817a335ac33dfbbecbce69217062b9acac3b847012761db9a2f176f`

## ROOTSTAGING

Tool timeout: `660`; workdir: `/home/me0kmin27/.hermes/cache/scratch/keimirror-safe-http-reexec`.

```sh
sudo /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 /usr/bin/python3 -I -S -B -c 'import hashlib, os, stat, sys
from pathlib import Path
LIMIT = 16 * 1024 * 1024
def safe_read(path, limit=LIMIT):
    """Walk by directory descriptors: never follow even an ancestor symlink."""
    path = Path(path).absolute()
    if '"'"'..'"'"' in path.parts:
        raise ValueError('"'"'parent traversal'"'"')
    fd = os.open('"'"'/'"'"', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = nxt
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError('"'"'bounded single-link regular file required'"'"')
            data = bytearray()
            while len(data) <= limit:
                block = os.read(leaf, min(65536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(leaf)
            if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink):
                raise ValueError('"'"'input changed while captured'"'"')
            return bytes(data)
        finally:
            os.close(leaf)
    finally:
        os.close(fd)
script, expected = sys.argv[1:3]
data = safe_read(script)
if hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit("bootstrap SHA256 mismatch")
sys.argv = [script] + sys.argv[3:]
__file__ = script
exec(compile(data, script, "exec"), globals())
' /home/me0kmin27/.hermes/cache/scratch/pr45-provenance-frozen-11b065c6761e-v2/source/root_stage.py ef37b412d817a335ac33dfbbecbce69217062b9acac3b847012761db9a2f176f stage /var/lib/keimirror-pr45-11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415 11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415 --source /home/me0kmin27/.hermes/cache/scratch/pr45-provenance-frozen-11b065c6761e-v2/source --artifacts /home/me0kmin27/.hermes/cache/scratch/pr45-provenance-frozen-11b065c6761e-v2/artifacts
```

## FORWARD

Tool timeout: `660`; workdir: `/home/me0kmin27/.hermes/cache/scratch/keimirror-safe-http-reexec`.

```sh
sudo /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 /usr/bin/python3 -I -S -B -c 'import hashlib, os, stat, sys
from pathlib import Path
LIMIT = 16 * 1024 * 1024
def safe_read(path, limit=LIMIT):
    """Walk by directory descriptors: never follow even an ancestor symlink."""
    path = Path(path).absolute()
    if '"'"'..'"'"' in path.parts:
        raise ValueError('"'"'parent traversal'"'"')
    fd = os.open('"'"'/'"'"', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = nxt
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError('"'"'bounded single-link regular file required'"'"')
            data = bytearray()
            while len(data) <= limit:
                block = os.read(leaf, min(65536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(leaf)
            if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink):
                raise ValueError('"'"'input changed while captured'"'"')
            return bytes(data)
        finally:
            os.close(leaf)
    finally:
        os.close(fd)
script, expected = sys.argv[1:3]
data = safe_read(script)
if hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit("bootstrap SHA256 mismatch")
sys.argv = [script] + sys.argv[3:]
__file__ = script
exec(compile(data, script, "exec"), globals())
' /var/lib/keimirror-pr45-11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415/root_stage.py ef37b412d817a335ac33dfbbecbce69217062b9acac3b847012761db9a2f176f forward /var/lib/keimirror-pr45-11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415 11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415
```

## ROLLBACK

Tool timeout: `660`; workdir: `/home/me0kmin27/.hermes/cache/scratch/keimirror-safe-http-reexec`.

```sh
sudo /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 /usr/bin/python3 -I -S -B -c 'import hashlib, os, stat, sys
from pathlib import Path
LIMIT = 16 * 1024 * 1024
def safe_read(path, limit=LIMIT):
    """Walk by directory descriptors: never follow even an ancestor symlink."""
    path = Path(path).absolute()
    if '"'"'..'"'"' in path.parts:
        raise ValueError('"'"'parent traversal'"'"')
    fd = os.open('"'"'/'"'"', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = nxt
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError('"'"'bounded single-link regular file required'"'"')
            data = bytearray()
            while len(data) <= limit:
                block = os.read(leaf, min(65536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(leaf)
            if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink):
                raise ValueError('"'"'input changed while captured'"'"')
            return bytes(data)
        finally:
            os.close(leaf)
    finally:
        os.close(fd)
script, expected = sys.argv[1:3]
data = safe_read(script)
if hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit("bootstrap SHA256 mismatch")
sys.argv = [script] + sys.argv[3:]
__file__ = script
exec(compile(data, script, "exec"), globals())
' /var/lib/keimirror-pr45-11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415/root_stage.py ef37b412d817a335ac33dfbbecbce69217062b9acac3b847012761db9a2f176f rollback /var/lib/keimirror-pr45-11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415 11b065c6761eec6fb66238f61bc057902247686d9297c52a2a5efb7492acd415
```

