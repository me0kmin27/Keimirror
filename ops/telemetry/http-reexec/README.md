# HTTP module re-exec transaction — DRAFT / DO NOT DEPLOY

This is an **unvalidated operational candidate**, not a completed production
release. No production signal, file replacement, package action, unit change,
restart, or deployment was performed for this PR.

## Blocking gates

1. Independent review blocked the old harness because it launched unverified
   artifacts before preparation. The provenance-only fix and explicit replacement
   commands in `ROOTSTAGING.md` require parent delta review and fresh normal tool
   approval. They have NOT been submitted/executed. The original pending command
   is invalid for these changed bytes and its 180-second timeout is insufficient.
   Forward/rollback GREEN remains unrun; historical mechanism tests do not count.
2. Fresh read-only inventory confirms mirror2's `asn.conf`, `nginx.conf`,
   configuration ancestors and existing include links have operator authority
   (UID/GID 1000); this is **not evidence of a new vulnerability**. The narrowly
   scoped preservation policy is now implemented and frozen in
   `host-readonly-configs.json`: 31 exact existing file/link entries on host 20,
   none on host 19. Review these fresh bindings before preparing an apply input.
   Never chmod/chown or rewrite operator-owned configuration.
3. Both hosts' existing root-owned archive hashes, active/enabled telemetry
   timers, actual document root and 11-byte public HTTP download were freshly
   verified. `host-baselines.json` records actual source-file and response hashes;
   it is **not ready-to-apply input**. The mirror lastupdate file can change with
   synchronization: refresh rather than reuse stale download approval. HTTPS
   checks separately failed (curl 60 on host 19, curl 7 on host 20); no insecure
   TLS fallback was used and HTTP success is not an HTTPS success claim.
4. Real privileged interruption/resume, syntax/startup failure and long-client
   deadline acceptance remain unrun. Nonprivileged coverage now exercises real
   atomic/fsync journal persistence, before/after dangerous orchestration windows,
   real disposable file restoration, and an actual executor-only SIGALRM.
   The 270-second forward budget plus reserved maximum 180-second recovery uses
   one persisted absolute 450-second ceiling; recovery/resume cannot reset it.
   Phase alarms also interrupt blocking Python operations, and every subprocess
   timeout is clipped to the active absolute deadline. No timeout kills nginx,
   workers or a cgroup. Kernel uninterruptible I/O/scheduling is not a realtime
   guarantee; privileged runtime validation is still required.

The complete user acceptance request is therefore **not yet satisfied**.

## Artifact provenance

`review-manifest.json` pins the script, tests and harness, reviewed source
`345717faaf50fa82ab8adcab0c4f84ebdcdeeaed`, and main
`85fc9c12a25a3c2d1fadb691b4082c55a7744cb6` (merged PR #43).
No files under `ops/telemetry/nginx-live/` or `telemetry/` are changed.

The `.deb` is checked against its exact reviewed digest, opened without
installation, and its extracted module checked against the module digest.
The versioned module is manually managed: **dpkg metadata stays unchanged**.
The old mapping pathname and private backup are retained. Package inventory is
compared at preparation, preflight and final receipt.

`project-live.versioned.conf` tracks the intended sole load directive. The
installer replaces the existing load fragment atomically; it does not add a
second `load_module` or overwrite an already mapped module.

## Implemented candidate flow

- `prep`: root-only read-only discovery and exclusive private stage creation;
  no `nginx -T`, no signals and no application writes. It records machine,
  boot/netns, PID/start identity, worker roles, listener inodes, executable and
  module hashes, expanded configuration metadata, systemd lifecycle and timers.
  Backup blobs and manifest are root-only 0600 under a 0700 directory.
- `apply`: explicit manifest hash, fresh preflight and cross-stage flock;
  versioned module copy, atomic loadconf switch, real `nginx -t`, USR2,
  oldbin/new PID and ancestry/cgroup gates, fresh module inode mappings,
  listener inheritance, worker readiness, then old WINCH.
- Acceptance checks a fresh reader master/epoch, transition null, stable next
  identity/epoch, bounded download and unique project 404. It records production
  uncertainty rather than claiming background traffic produced exact zero.
  Exact error-accounting semantics belong to isolated same-artifact tests.
- Old workers drain naturally. Only then does write-ahead `commit-intent`
  precede old QUIT. New MainPID adoption and service/public-path health are
  checked. A post-commit failure must not signal a departed old PID.
- `rollback`: restore exact loadconf bytes/uid/gid/mode/mtime, syntax-check,
  old HUP and old worker readiness **before** new QUIT. While the new master
  exists, old HUP restarts its workers without rereading configuration. A durable
  `new-quit-intent` resume verifies old readiness without sending old HUP again. No TERM/KILL or broad
  process matching exists in the transaction. Drain timeout retains processes.
- `reconcile`: only reconciles a persisted commit-intent/new identity; it does
  not guess whether to signal an old generation. Other interrupted phases use
  conservative rollback. An ambiguous USR2 intent without a published verified
  new master is an explicit incomplete-reconciliation error, not rollback success;
  USR2 is never repeated. Once old QUIT may have happened, switching back to the
  legacy module requires a separately reviewed new upgrade, not HUP of a retired
  master. Same-boot/netns only; reboot recovery is not claimed.

Signal delivery uses a pidfd opened before validating PID/start identity, so
PID reuse cannot retarget a signal. Mappings require matching inode/device,
not just a path substring. Failed old readiness never quits the new master.

## Connection policies

Both hosts were inspected read-only in this task: no configured QUIC listeners
and no `worker_shutdown_timeout` directive were found. Current worker baselines
were mirror/19 = 32 and mirror2/20 = 44.

Any QUIC/UDP listener fails closed, **including** a configuration with
`quic_bpf on`: this implementation has not proved QUIC BPF ownership/migration.
Any nonzero worker shutdown timeout also fails closed because nginx may close
clients when it expires. The installer never rewrites that production policy.

## Commands (review candidate only)

Installer and harness source must first be staged as reviewed root-owned files
under root-controlled, non-world-writable directories. Direct privileged execution
from this operator-owned checkout fails closed before the harness creates a unit.
The readonly-config exception NEVER applies to installer/source/package/stage,
reader execution inputs, candidate module, or the sole writable loadconf. The
production loadconf is UID/GID 0, mode 0664 (write only by root/root group), under
root-owned non-world-writable directories; its metadata is preserved.

Optional `readonly_configs` is an exact path-to-frozen-metadata map: file SHA256,
UID/GID/mode/mtime/device/inode, and an individually frozen ancestor directory
UID/GID/mode/device/inode map. Existing symlinks additionally pin link target and
inode/timestamps. All other paths retain the root trust gate. Included paths,
links, owners and hashes are checked before modification/signals and completion.
The transaction writes only its stage, new module and root-owned loadconf; it has
no write/restore/chmod/chown path for `asn.conf` or any preserved existing config.

The input JSON must be a root-owned private file. It supplies exact existing
paths (`binary`, `config`, `prefix`, `pid`, `load`, `old_module`, `status`,
`reader_dir`), unit, hostname, worker count, reviewed package staging path,
content-addressed `new_module`, load SHA256, timer list, independently retained
backup path/digest map, byte/hash-verified bounded HTTP probes, and a project
404 URL prefix. Inspect `isolated.py` for the complete disposable input shape.

```sh
python3 transaction.py prep --inputs /PRIVATE/inputs.json --stage /PRIVATE/new-stage
# Only after review and approval of the resulting manifest SHA256:
python3 transaction.py apply --stage /PRIVATE/new-stage --approve-manifest-sha256 SHA256
python3 transaction.py rollback --stage /PRIVATE/new-stage --approve-manifest-sha256 SHA256
python3 transaction.py reconcile --stage /PRIVATE/new-stage --approve-manifest-sha256 SHA256
```

Use an executable filesystem for the versioned module, not noexec `/run`.
Retain private stage and journal across executor/transport interruptions.
Never delete a collision to retry. Do not reuse an old host manifest after
worker, configuration, binary, unit, boot or namespace changes.

## Tests and evidence

```sh
python3 -m unittest discover -s tests -p test_http_reexec.py -v
python3 -m unittest discover -s tests -p 'test_*.py' -q
# Provenance regression tests (no privileged commands):
python3 -B -m unittest discover -s tests -p test_harness_provenance.py -v
# Privileged replacements are recorded explicitly in ROOTSTAGING.md.
# Do not invoke isolated.py directly from the operator checkout.
```

ARTIFACTS contains exact `nginx`, `old.so`, `candidate.deb`, and a `reader/`
directory with unchanged `http_rates.py` and `private_reasons.py`. The harness
creates only a UUID-scoped separate unit, private executable artifacts and a
128 KiB synthetic loopback file. Its cleanup stops only that test unit.
The rollback case injects a deadline failure after WINCH. Neither test result
is presently claimed as passing.

Previous reviewed transaction suite: **250 tests, OK, 3 skipped**
(**30 transaction tests**, including a matrix of before/after interruption windows).
Feature REDs preceded readonly policy, parent-chain preservation, source gates,
absolute deadline implementation and ambiguous/resumed rollback fixes. Existing
behavior matrix cases are regression coverage, not separately claimed REDs.
Generated HTML/runtime freshness also passed. Browser results are reported by CI,
not inferred from the Python suite. The transaction review identified no blocker;
the harness provenance delta still requires parent review. Current-head test/CI
receipts are recorded separately; no root-runtime success is inferred.

Official nginx `control.html` upgrading section was retrieved and read by the
parent using **curl**, saved as `nginx-control-parent.html` in the parent scratch
directory. No `web.run` retrieval is claimed. It confirms USR2/WINCH, old-HUP
worker revival without config reread, graceful new QUIT, and old QUIT commit.
Previous exact
artifact tests proved USR2/WINCH, old-HUP-before-new-QUIT rollback, full slow
responses and automatic systemd PIDFile adoption on both hosts. Those receipts
remain historical mechanism evidence, not validation of this draft installer.
