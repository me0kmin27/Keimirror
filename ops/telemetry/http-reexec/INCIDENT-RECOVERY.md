# HTTP re-exec incident: scoped repair and remaining gate

## Historical production outcome (2026-10-30)

Baseline: PR45 merge `27269689baa4ea664c4322bfdf8ecbf244b811d5`.
The production transaction failed while acquiring the new PID publication:
`read(pidfile, 32)` observed changing metadata; automatic recovery subsequently
encountered an absent PID file during the rename/publication interval.

At that point both masters existed and the **original 32 old workers were still
serving**. No WINCH had happened. The old rollback sent HUP anyway, causing the
old master to allocate another worker cycle while original process slots remained
occupied. The fully loaded Nchan module aborted those additional workers:

```
memstore.c:704: nchan_store_init_worker: Assertion `procslot_found == 1' failed.
```

The old master respawned aborting children. New-master workers stayed stable.
The original transaction exceeded its persisted recovery deadline. It did not
commit or successfully roll back. Its journal, original errors and deadlines
were preserved; this repair does not reopen or rewrite it.

Separate authorized incident recovery eventually completed forward after the
last old client worker disappeared naturally. Recorded post-adoption evidence
shows new master PID 1539134, all 32 new worker PID/start identities stable across
30 checks, systemd adoption, and all 69 protected entries preserved. This is
**incident recovery**, not retroactive transaction success. An initial probe of
a rotating source file and a system-CA check of the existing self-signed origin
certificate failed their own gates; final sustained verification used immutable
content and the baseline-pinned certificate. No Nchan message-continuity claim.

## Fix scope

1. A dedicated PID-publication reader keeps the general immutable-file reader
   strict. It retries only missing/empty/in-flight metadata or inode publication
   and incoherent PID pairs under the current workers/recovery deadline. It
   validates no-follow bounded regular root-owned files, ASCII PID syntax,
   oldbin identity, new start time, parent, executable, cgroup and a second
   coherent PID/process observation. Invalid input or lineage is not retried.
   Ambiguous USR2 remains an incomplete-recovery error, never a second USR2.
2. A persisted pre-WINCH rollback strategy proves the original worker identities,
   roles and listeners, **without HUP**, before new QUIT. Replacement workers
   cannot satisfy this proof. After possible WINCH, every old worker must drain
   naturally before one HUP. Partial drain retains the new generation and an
   incomplete journal on the unchanged deadline. A pending HUP intent is never
   resent. A recovery exception cannot replace the primary forward exception.
3. Nchan configuration or executable mappings fail preflight before installation
   or signals, including renamed/static module signatures. No runtime flag can
   approve it. A tested all-phase design or separately reviewed forward-only
   transaction is required before lifting this gate. Long-client partial drain
   does not become safe merely because fully drained slot reuse works.

No C, module, package, nginx binary, reader, authentication or host deployment
bytes changed. Existing package/module digests remain pinned. Earlier review
approval does not authorize this changed transaction or its refreshed manifest.

## Executed full-module reproduction

`tests/http_reexec_capture.py` captures a specifically selected host's mapped
module files, binary, ELF dependencies and packaged Perl/Lua runtime support via
read-only commands. The actual host20 capture contained **22 loaded dynamic
modules**, not an assumed module count, including Nchan and the original project
module. All were loaded in a separate local Debian 13 root chroot on a unique
loopback port. Archive, per-file and candidate hashes were verified before
execution. Nothing was installed or signaled on production hosts.

Nchan SHA256:
`77950e6c4aa8ba17636c40a651b1ba50469787f8e11f2045cb04e13432b18e04`

Executed transaction SHA256:
`4c84aff6e55035cd625df0288c2bc658d3738e075ecc7eb811c2b00a3bd2dad6`

The checked-in `incident-runtime-evidence.json` contains actual parsed receipts:

| Case, initially two workers | Actual result |
|---|---|
| Baseline source, USR2 then old HUP before WINCH | Exact Nchan assertion, SIGABRT/respawn, original workers retained, new workers stable; incomplete rollback |
| Fixed source, USR2 then pre-WINCH rollback | No HUP; new QUIT; same original worker PID/start set, restored PID publication, no abort, protected body intact |
| Fixed source, WINCH then all old workers gone | HUP/QUIT succeeds with fresh stable old workers; no Nchan assertion |
| Fixed source, WINCH with a held slow client | Same deadline expires; no HUP, no new QUIT; new generation stable, incomplete rollback |

These execute the actual Transaction rollback and Linux identity/readiness
primitives. The test-only backend substitutes chroot syntax execution and local
PID restoration for systemd adoption, and bypasses the **production Nchan
preflight gate solely to reproduce the forbidden primitive**. This is not a
production apply test or proof of Nchan channel/message continuity.

### Remaining runtime blocker

The attempted 32-worker runs stopped during initial fixture worker inventory
(`child inventory changed`), before the rollback experiment. The harness now
has a bounded startup-only inventory retry and cleanup registration before
startup, but those changes have not yet been re-executed. The two private local
masters from those failed starts require the already-submitted identity-guarded
cleanup command's normal tool approval. It was not rerouted. Until cleanup and
32-worker reruns succeed, **32-worker runtime acceptance remains incomplete**.
The executed two-worker harness hash and this limitation are recorded separately
from the tested transaction source hash.

Initial fixture failures also identified required Perl support/ABI directory
links and Lua resty runtime files; those dependencies were captured rather than
removing modules, changing versions or disabling module initialization.

## Tests, review and rollout path

The PID metadata race was first reproduced using real file IO during the read.
New pre-WINCH tests first observed the unwanted HUP and missing compatibility
gate. Regression coverage includes the exact 32-worker PID/start identity check,
partial generation refusal, strict file types/ownership/ASCII/length, lineage,
coherent PID pairs, unchanged deadline, original-error preservation and the
existing interruption/resume matrix. Unit tests are not a substitute for the
remaining 32-worker runtime gate.

Local full suite: **296 tests, OK, 3 skipped**; generated HTML/runtime freshness
and `git diff --check` passed. Browser coverage is reported by actual PR CI,
not inferred from the Python suite. No independent review approval is claimed.

1. Approve/complete the pending private local cleanup, verify absence, and rerun
   the full-module 32-worker cases with the corrected harness.
2. Independently review the exact changed transaction, source pins, tests and
   sanitized runtime evidence; verify actual PR-head CI separately.
3. Keep production Nchan blocked. Design and prove all-phase recovery or a
   constrained forward-only contract separately; do not silently remove Nchan.
4. Only then prepare new host-specific manifests and explicit rollout approval.
   Do not reuse the expired original journal or reset its budget. Host20 remains
   undeployed; its production configuration, credentials and signals are untouched.
