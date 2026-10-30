# ROOTSTAGING — proposed replacement, NOT executed or approved

The preceding provenance delta at `0f1895a` passed independent review and exact
local forward/rollback execution. Those runs did not prove the complete requested
acceptance: 403/416 and zero uncertainty were missing, and cleanup deleted the
production acceptance receipt. This replacement changes ONLY the isolated fixture
and supporting tests/manifests/docs; transaction.py, readers and package stay pinned.

Parent independent delta review and the normal tool approval gate must approve each
exact command below before execution. The source author cannot self-approve this
privileged harness change. Do not wrap commands in shell `-c`, remove flags, or infer
approval from source CI. The explicit Python `-c` bootstrap must pass the normal
script-execution approval. None of THESE replacement commands has been submitted
or executed. No production mutation or merge is authorized.

The isolated fixture reuses nginx-live/tests/test_errors.py's real deny-all 403,
missing-static 404 and unsatisfiable Range 416 cases, plus static 200. Before USR2,
old 403 must show uncertain >= 1. After new readiness and old WINCH, old worker
accept-listener FDs must be gone before any candidate probe; every raw status
sample must identify the new worker set. Each candidate case must show uncertain=0,
active_uncertain=0, exact request RX and complete header+body TX, with strictly
increasing BODY TX while its completion log has not advanced. Normal transaction
acceptance remains permissive for production background uncertainty.

Before stopping the unit or deleting runtime files, preserve the exact production
acceptance.json and bounded raw status/request/response/completion-log bytes with
wall and monotonic timestamps in OUTPUT-parent/{forward,rollback}-acceptance/.
These exclusive directories are mode 0700, receipts mode 0400, and sha256.json
binds both acceptance.json and isolated-proof.json. All raw evidence stays private;
public stdout contains only case/pass/body-size/phase/cleanup, never addresses.

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
reused or repaired. Outputs are private target/forward.json and rollback.json, plus the retained
acceptance directories described above. The new manifest-suffixed target is
exclusive: never reuse the prior root stage.

Root manifest SHA256: `833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76`

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
' /home/me0kmin27/.hermes/cache/scratch/pr45-acceptance-frozen-833a8170aa55/source/root_stage.py ef37b412d817a335ac33dfbbecbce69217062b9acac3b847012761db9a2f176f stage /var/lib/keimirror-pr45-acceptance-833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76 833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76 --source /home/me0kmin27/.hermes/cache/scratch/pr45-acceptance-frozen-833a8170aa55/source --artifacts /home/me0kmin27/.hermes/cache/scratch/pr45-acceptance-frozen-833a8170aa55/artifacts
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
' /var/lib/keimirror-pr45-acceptance-833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76/root_stage.py ef37b412d817a335ac33dfbbecbce69217062b9acac3b847012761db9a2f176f forward /var/lib/keimirror-pr45-acceptance-833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76 833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76
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
' /var/lib/keimirror-pr45-acceptance-833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76/root_stage.py ef37b412d817a335ac33dfbbecbce69217062b9acac3b847012761db9a2f176f rollback /var/lib/keimirror-pr45-acceptance-833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76 833a8170aa5581291954fb729fdb598f8f8c2c240fd642455e7e7243ddff2c76
```

