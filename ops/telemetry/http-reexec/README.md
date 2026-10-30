# HTTP module re-exec transaction — DRAFT / DO NOT DEPLOY

This is an **unvalidated operational candidate**, not a completed production
release. No production signal, file replacement, package action, unit change,
restart, or deployment was performed for this PR.

## Blocking gates

1. The new isolated transaction execution command is awaiting tool approval.
   Its feature-absent RED was executed; forward and rollback GREEN have **not**
   run. The earlier `upgrade-reexec` mechanism tests are not a substitute for
   exercising these new installer bytes.
2. Read-only historical inventory records a non-root-owned existing
   `/etc/nginx/conf.d/asn.conf` on mirror2. This candidate deliberately rejects
   untrusted configuration paths. Resolve that policy against fresh inventory
   in review; do not chmod production files or silently weaken the gate.
3. Fresh host-specific retained-backup bindings, real download byte/hash inputs,
   full private host manifests, and independent installer review remain required.
   `host-baselines.json` is discovery evidence, **not ready-to-apply input**.
4. Before approval, complete real interruption/resume, syntax-failure, failed
   startup, long-client drain and independent-phase deadline tests. Review the
   cooperative timeout boundary: subprocesses have deadlines, but filesystem
   operations are not hard-preempted. A retained executor must not kill nginx
   clients or its cgroup when its own command deadline expires.

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
  old HUP and old worker readiness **before** new QUIT. No TERM/KILL or broad
  process matching exists in the transaction. Drain timeout retains processes.
- `reconcile`: only reconciles a persisted commit-intent/new identity; it does
  not guess whether to signal an old generation. Other interrupted phases use
  conservative rollback. Same-boot/netns only; reboot recovery is not claimed.

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
# Authorized disposable root/systemd tests, NOT production:
sudo python3 -B ops/telemetry/http-reexec/isolated.py ARTIFACTS OUTPUT.json --case forward
sudo python3 -B ops/telemetry/http-reexec/isolated.py ARTIFACTS OUTPUT.json --case rollback
```

ARTIFACTS contains exact `nginx`, `old.so`, `candidate.deb`, and a `reader/`
directory with unchanged `http_rates.py` and `private_reasons.py`. The harness
creates only a UUID-scoped separate unit, private executable artifacts and a
128 KiB synthetic loopback file. Its cleanup stops only that test unit.
The rollback case injects a deadline failure after WINCH. Neither test result
is presently claimed as passing.

Completed local suite: **234 tests, OK, 3 skipped** (14 transaction tests).
RED failures were observed for policy, orchestration, config/file primitives,
pidfd gate, mapping inode gate and interrupted rollback. Later additional
orchestration cases are regression coverage, not claimed independent REDs.

Official mechanism context previously verified by the parent: nginx control
`UPGRADING_EXECUTABLE` and `ngx_http_v3_module` / `quic_bpf`. Previous exact
artifact tests proved USR2/WINCH, old-HUP-before-new-QUIT rollback, full slow
responses and automatic systemd PIDFile adoption on both hosts. Those receipts
remain historical mechanism evidence, not validation of this draft installer.
