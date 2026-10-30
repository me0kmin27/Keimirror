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

## Whole-observation churn retry (local follow-up to `90c9cf9`)

**Not deployment-approved.** The recovery-v3 exact-source canary failed: 12 ticks,
7 valid state envelopes, 2 numeric repository-tick pairs out of 168, and 8 failed
independent proof reads. Its manifest and frozen source remain unchanged. Local
comparison verified that recovery-v3 `rates.py` and `rpc.py` are byte-identical to
this branch's original `90c9cf9` sources, not a different installed implementation.

### What the existing trace does and does not establish

All eight recorded independent proof exceptions were ENOENT in `rpc.holders`:
three at FD-directory enumeration (old line 175), five at FD readlink (old line
180); five occurred in the first holder scan, three in the second. When such a
failure occurred inside the old sampler, its proof handler applied no RPC
exemptions. Separately,
`rates.processes` reads every enumerated PID's stat/cmdline without skipping
vanished entries; an ENOENT there returned global `{}`. The new on-disk fixture
reproduces that path. The historical sampler did not persist caught exception
reasons, so assigning all five historical `{}` results to it would be speculation.

A successful **separate** proof is not complete sampler coverage. Proof success
on ticks 2, 4, 8 and 11 established one RPC exclusion each, at a different instant
from the default sampler. Only tick 4 of those had a numeric row. The trace saved
rows and envelope timestamps, not the sampler's repository inventories, its own
RPC proof outcome, or remaining unproved socket counts. It cannot establish
whether other ownerless sockets remained in those observations.

The exact predicate explains why this is possible without fabricating zeros:
`empty_complete` becomes false for any unproved ownerless nonterminal socket or
unmatched rsync/ssh/openssl holder. Repository-specific ambiguity and previously
owned cookies losing attribution also suppress coverage. A valid state envelope
can therefore contain no covered repository, or only an active attributed one.
Even a complete new empty baseline is null until the next adjacent valid empty
observation. HTTP combination still requires TCP coverage and does not turn an
HTTP-only component into a project total. A new regression proves that one valid
RPC exclusion **plus another unproved ownerless live socket** remains unknown.
Nothing here claims the historical host had that second socket: the needed
inventory was not recorded.

### Scoped implementation

- Production sampling allows **three total attempts**, with no sleep and one
  absolute monotonic deadline set at entry (`start + 2s`). Both `processes` ENOENT
  and holder-inventory ENOENT discard the entire attempt. The next attempt starts
  from boot/process/root identity, ss, diagnostic counters, both RPC reads and
  both holder inventories; no first diagnostic or partial holder set is reused.
- The retry owner is `rates.sample`, not `rpc.exclusions`. A standalone exclusions
  call still fails on incomplete inventory: it cannot safely replace the caller's
  already acquired first diagnostic. `ObservationChurn` retains errno and marks
  holder failures for the outer full restart, rather than being swallowed as an
  ordinary optional-helper failure.
- Only FileNotFoundError/ENOENT retries. Permission, parse, bounds and other I/O
  errors do not retry into success. Missing/denied helper proof retains its
  original independent-attribution behavior, with no exemption or fake zero.
- A context-local deadline reaches proc checkpoints, ss selector/child wait,
  netlink waits and each client RPC connect/send/receive timeout. None receives a
  fresh two-second allowance per attempt. Final output is rejected if the whole
  call exceeds the deadline. Context is restored in `finally`; flat installs and
  injected reader signatures are unchanged.
- This is a cooperative acquisition deadline, **not OS-level preemption** of an
  in-flight proc syscall, an arbitrary injected reader or subprocess cleanup.
  A late operation cannot publish a valid sample, and no following acquisition
  starts after a checkpoint observes expiry. Hard wall-clock termination of an
  uninterruptible syscall is not claimed. Existing external service bounds remain
  necessary; no signal handler, background worker or new privilege was added.
- No changes to attribution, RPC identity matching, previous-cookie invalidation,
  first-sample nulls, adjacent-window rate calculations or aggregation. A retry
  uses fresh counters and its actual observation timestamp, not old rates with
  a new timestamp. Exhausted churn returns all-null rows and `{}`; recovery needs
  two valid adjacent observations, just as before.

### Local verification and remaining gate

Before implementation, an actual fixture FD unlink made the required recovered
empty baseline fail. A second RED demonstrated that an expired total budget still
started ss. Both passed after their corresponding changes. A frozen-source
negative control additionally ran the FD, vanished-process and deadline regressions
against the unchanged recovery-v3 modules: all three failed as expected (no test
errors). A final RED/GREEN closed the empty-inventory path that resumed a proc
read after optional RPC proof exceeded the shared deadline. Other new cases are
regression controls, not claimed as earlier RED runs.

Local results:

- Python discovery: **206 tests, 203 passed, 3 existing nginx-binary skips**.
- The nine standalone HTTP test functions (not collected by unittest discovery)
  also passed via `unittest.FunctionTestCase`; pytest is not installed locally.
- RPC coverage file: **20 passed**, including continuous churn, permission/parse/
  other I/O errors, second-holder failure, changed socket identity between
  attempts, previous-owner loss, fresh counters/timestamp and remaining unknowns.
- Real sleeping child and silent Unix SEQPACKET peer verify waits are clipped to
  a 50ms remaining budget; netlink timeout wiring is checked separately. Existing
  exact-2s acceptance and over-2s rejection tests still pass.
- Browser suite: **155 passed**. Dependencies were restored from the local npm
  cache with `npm ci --offline --ignore-scripts`. Generated-output freshness and
  `git diff --check` passed. No browser/test acceptance threshold was changed.

Logs, frozen-source negative control and final `source-manifest-v4.1.json` are in the
private `keimirror-rpc-integration-evidence/retry-v4` directory; prior evidence is
not overwritten. These are local fixture and regression results, **not evidence
that normal host churn now meets operational coverage**. No remote operations,
push or deployment were performed by this follow-up. Parent source review and a
new exact-source canary are required before deployment. Re-run the unchanged
fixed-tick acceptance and fresh missing-helper control against the new committed
bytes; preserve every null and actual returned state. The new trace should record
sanitized same-sampler coverage reasons/counts (no addresses, argv or FD paths)
to distinguish proof loss from residual unproved sockets, rather than inferring
that distinction from the separate diagnostic proof.
