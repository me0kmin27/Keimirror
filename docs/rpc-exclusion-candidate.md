# Namespace-proved RPC exclusion candidate

This is a local review candidate, **not a deployment or evidence that production
traffic nulls are fixed**. Base: `72f27994df643609aa11b351b1ad18f7613785ac`.

## Concrete path

- `rpc_helper.py` is a separate root-owned service, not collector setup code.
  It unshares only the mount namespace, checks that net/user namespaces stay
  unchanged, makes mounts recursively private, and creates a **fresh** sysfs
  mount under `/run/keimirror-rpc/sysfs`. Failure is fatal, without an inherited
  `/sys` or bind-mount fallback. It verifies the exact mount root, filesystem,
  private propagation and per-mount `ro,nosuid,nodev,noexec`. A shared `rw`
  superblock is permitted; no remount is attempted.
- Before binding the IPC socket it drops SYS_ADMIN/SETPCAP, clears the complete
  bounding/inheritable/ambient sets, sets no-new-privileges, and verifies the
  remaining effective/permitted mask is exactly `0x80004`. This existing
  proc-visibility mask is needed for peer namespace/start-time verification;
  CAP_SYS_PTRACE is powerful, not intrinsically read-only. No subprocess or
  arbitrary path/command request is supported.
- The runtime directory is root:root 0700; the Unix SEQPACKET socket is 0600.
  Both peers require root SO_PEERCRED, stable PID/start ticks and the same netns.
  Every response is a new bounded read, tied to a random per-request nonce,
  boot ID, netns and monotonic request interval. There is no proof file, shared
  proof cache, endpoint journal output or public endpoint field. Both processes
  disable dumpability; service units also set `LimitCORE=0`.
- Production `rates.sample()` automatically tries this fixed private socket.
  In each observation it joins two fresh RPC transport reads with two diagnostic
  observations and two complete bounded `/proc/*/fd` readlink inventories.
  Only a unique exact family/address/port tuple, stable positive socket inode,
  nonzero/non-sentinel cookie, UID 0, ESTABLISHED state and stable connected RPC
  transport inode can exempt an ownerless socket. There is **no port whitelist**.
  Endpoint bytes remain transient. Scoped/link-local RPC addresses fail closed.
- The full sampler observation and adjacent rate interval retain the existing
  **two-second monotonic** bound. Every adjacent observation independently needs
  proof; the old five-second canary predicate is not used. Missing helper,
  namespace/boot mismatch, stale reply, changed tuple/cookie/inode, FD scan gap,
  incomplete diagnostic dump or capability failure cannot create empty coverage.
  Known rsync attribution remains independent of helper availability. Previous
  attributed cookies still invalidate their previous repository when ownership
  disappears, even if the current socket matches RPC.
- HTTP aggregation is unchanged: missing TCP coverage still yields null, never
  HTTP-only totals or synthetic zero. Verified empty observations retain their
  existing sampled-live semantics, not whole-interval byte accounting.

## Installation contract (not executed)

Install the reviewed `rates.py`, `rpc.py`, `rpc_helper.py` together into the
existing root-owned `/opt/keimirror-telemetry/` flat layout. Do not update only
`rates.py`. Install the helper unit and the collector unit's core-limit change;
collector capabilities are unchanged. Explicitly enable/start the helper after
separate privilege/deployment approval. No collector Requires dependency is
introduced: helper failure must not prevent ordinary collection. The helper
unit orders itself before the collector when both are started in one transaction;
its first successful response establishes readiness. Until then, fail closed.

`RuntimeDirectoryPreserve=no` owns restart cleanup. A pre-existing mountpoint or
socket is rejected, not reused or unlinked by the helper. Private namespace
mounts disappear when the service exits. Do not add a broad cleanup command,
CAP_SYS_ADMIN to the collector, or a systemd bind-mounted inherited `/sys`.

Rollback: stop/disable only the helper, restore the previously reviewed sampler
and collector unit, reload systemd. Do not alter nginx, synchronization schedules,
mirror data, account/SSH configuration, or current source PR40.

## TDD evidence and acceptance limits

Observed RED -> GREEN vertical slices in this implementation session:

1. Missing `rpc_reader` sampler seam -> proved ownerless RPC allows a second
   complete empty observation; serialized rows/state have no endpoint fields.
2. Missing opt-in diagnostic endpoints -> actual loopback socket tuple matches
   `getsockname`/listener and default diagnostics still omit addresses.
3. Missing sysfs reader -> on-disk transport fixture reads fresh changes and
   missing source fails (no inherited source fallback).
4. Missing IPC reader -> real Unix SEQPACKET request/reply, credential/privacy
   checks and nonce/boot/netns/monotonic rejection.
5. Missing helper setup -> syscall-contract fake verifies private mount then
   capability drop; denied mount, wrong namespace and failed drop never return
   a reader; ro mount/rw superblock accepted, unsafe flags rejected.
6. Missing runnable helper -> denied setup never reaches serving; real
   unprivileged unshare is denied with unchanged mount table/namespace.
7. Production still returned null despite fixture proof -> automatic live helper
   acquisition twice per observation, no cached proof after helper failure.
8. Flat installed imports failed with `ModuleNotFoundError: telemetry` -> actual
   relocated module imports work with the existing installed layout.
9. Missing helper unit -> separate capabilities/runtime/IPC contract passes
   `systemd-analyze verify`; collector never gains SYS_ADMIN.
10. Missing collector core limit and piped-core protection -> unit check and a
    real subprocess PR_GET_DUMPABLE check pass.

Additional regressions cover exact 2s acceptance and >2s rejection, 5s rejection,
cookie/inode/state/UID changes, previous-owner invalidation, missing FD inventory,
boot/netns changes, stale adjacent samples, and refusing a partial HTTP total.

The first full-suite attempt caught a test-only umask leak from invoking helper
`main()` in-process. The test now mocks that process-global operation; the actual
service still sets 0077. No unrelated production logic was changed to hide it.

**Not yet exercised:** privileged startup of this exact helper under its unit,
actual root-to-read-mask capability drop in this new process, live RPC sysfs data
through the new IPC, host quota/cadence, restart cleanup under systemd, and real
in-flight rsync/HTTP-to-public-UI acceptance. Setup syscall mocks are not kernel
mount evidence. The earlier isolated canary is supporting feasibility evidence
only and does not certify these new bytes. Three existing nginx-dependent Python
checks require the actual/extracted nginx binary and remain skipped locally.
Independent review and a separately authorized bounded exact-source integration
canary are required before installation. No push, merge, service enablement,
remote deployment or host privilege changes were performed here.
