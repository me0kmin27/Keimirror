# Private collector reason codes (diagnostic candidate only)

This is observability, **not a numeric-coverage fix**. It does not establish
stable one-second production output. No production deployment is authorized by
this document. The nginx C module and its ABI, RPC helper source, public schema,
HTML, validity rules, timeouts and counter definitions are unchanged. In
particular both complete userspace FD scans remain mandatory for RPC exclusion;
absence of an ss owner is not proof that a kernel RPC socket lacks userspace FDs.

## Actual invocation and installation dependency

`collector.collect()` supplies optional `diagnostic(code, repository, count)`
callbacks to the actual TCP and HTTP sampler calls. Direct sampler users retain
the old signature behavior with `diagnostic=None`. Callbacks cannot change the
sampler's returned pair or swallow sampler exceptions. Callback `Exception`s
are ignored except the existing whole-tick `CollectionDeadline`; that exception
is shared with the collector and always re-raised. Process-control
`BaseException`s are not intercepted. No tracing,
extra proc scan, network query, per-event clock read, or source string is added.

The new **required Python dependency** is `private_reasons.py`. A future reviewed
installation must ship it alongside `collector.py`, `rates.py`, `http_rates.py`
and `rpc.py` in the existing flat directory. `rpc_helper.py` is unchanged but
imports the existing RPC reader transitively; keep this dependency available.
The relocated-module test checks this layout. Do not deploy individual changed
files without the new dependency. No service/unit or helper change is included.

The same collector writes `STATE_DIR/diagnostics.json`, never `public/`, after
its existing private cursor write. Atomic replacement uses a fresh **0600**
file owned by the collector (root under the shipped service). The existing
trusted root-owned state directory is the security boundary. The destination
is never opened/read, so an existing symlink/FIFO is replaced, not followed.
Serialization and filesystem errors are best effort and do not block the
collector's return. Existing whole-tick deadlines still apply to local I/O;
this is not a promise that arbitrary blocked filesystems are nonblocking.

No existing state version is changed. Old consumers never read this separate
sidecar. Its previous content is never parsed, migrated or accumulated. An old,
malformed or unwritable sidecar cannot influence rate validity. A failed write
may leave an old diagnostic file: **check `collected_at` before interpreting it**.
The sidecar and cursor are separate atomic files, not a two-file transaction.

## Private format and interpretation

`schema_version: 1`, `collected_at` equal to this tick's public collection time,
and `sources` containing `tcp`, `http`, `combined`. Each has `counts` (numeric
reason IDs encoded as JSON keys) and `repositories` (only the collector's fixed
repository allowlist, each with reason counts). TCP/HTTP additionally include
`state_present`, returned-state `monotonic`, and `observed_at` (TCP state `at`;
HTTP has no wall timestamp in its saved baseline, so this is null).

Counts saturate at 2147483647. Vocabulary and repositories are fixed, and the
maximal populated document is tested below 64 KiB. There is no unbounded event
history, raw PID, UID, inode, cookie, endpoint, argv, exception text or path.
Unknown state/time remains false/null; it is not synthesized from successful
attempts or the publication clock.

Counts cover **this invocation including discarded attempts**, not just its
accepted observation. Code 32 counts started attempts. Stage and leaf codes
can describe the same ENOENT; do not sum them as independent failures. State
presence describes the final returned baseline only. Ownerless count/state/UID
classes describe vetoing rows, not actual identity or a tuple-to-RPC proof.
They are marginal counts, not per-socket joins. Repository reason counts are
not rates and must never be used to override unknowns.

Authoritative numeric vocabulary is `Reason` in `telemetry/private_reasons.py`:

| Codes | Meaning / coverage |
|---|---|
| 1–6 | Empty-zero veto: unresolved ownerless row; established vs other active state; diagnostic UID root/nonroot/unavailable. No addresses or UID values. |
| 10–11 | ENOENT opening proc stat / cmdline. |
| 12–18 | Retry exhaustion; process-table, ss, diagnostic-reader or other ENOENT stage; exact RPC holder fd-directory open / FD readlink ENOENT. |
| 19–20 | Shared sampling deadline; other caught observation rejection (without exception text). |
| 21–25 | Baseline mismatch; invalid/expired pair window; missing prior repository or changed socket set; counter/identity transition; previously attributed cookie now uncertain. |
| 26–29 | Optional RPC proof unavailable; collector namespace changed; checked ownership changed; unclassified rsync-family transport veto. |
| 30–32 | First/second complete RPC holder pass ENOENT; started observation attempt. |
| 33–35 | Namespace-link acquisition/recheck ENOENT; attributed owner namespace/FD acquisition ENOENT; final held-FD/namespace recheck ENOENT. |
| 100–107 | HTTP uncertainty counter change, active uncertainty at either endpoint, byte counter regression, epoch change, expired/invalid pair window, missing/malformed prior baseline, registered/live worker mismatch, inter-sample identity change. |
| 108–110 | HTTP worker acquisition/recheck, transport read, or payload-decode failure. |
| 200–201 | Combiner's TCP or HTTP source has null/missing rate components. Both may occur in one tick. |

Remaining discrimination limits are deliberate: the generic TCP rejection and
other ENOENT code retain uncommon boot/config/delivery failures without raw
exceptions; counter vs identity and missing-prior vs set churn are grouped.
HTTP source-stage failures group their internal reasons. Combiner timestamp
skew/overflow are not separately instrumented. Most importantly, HTTP codes
100/101 identify the **Python rejection branch, not which C module branch
incremented uncertainty**. A module ABI/source diagnostic change needs separate
review. This candidate must not be described as complete per-C-branch coverage.

## Verification

Run from the repository:

```sh
python3 -m unittest discover -s tests -p test_private_reasons.py -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tools/build.py --check
```

New behavior followed RED/GREEN cycles: optional TCP callback/ownerless veto;
proc churn/exhaustion; source-stage, holder leaf and pair classifications; HTTP
rejection classes; actual collector sidecar; HTTP acquisition failures; RPC,
namespace and ownership distinctions; holder-pass/attempt distinctions; owner
FD and verification ENOENT stages. Missing keyword/enum/sidecar failures were
observed before their implementations. Privacy/saturation and symlink/FIFO/
atomic-failure tests subsequently verify existing writer guarantees, not new
claimed RED cycles. Tests compare instrumented and uninstrumented public rows
and baselines and verify callback exceptions cannot change sampler outcomes.
An additional RED/GREEN regression ensures an alarm delivered during the
callback cannot be swallowed by observer-failure isolation.

Tests that enter the default production RPC reader must isolate
`rpc.protect_memory`: its dumpability change is process-global. The fixture
mocks that startup boundary, while relocated CLI tests run in subprocesses.
No remote operations, deployment, source-helper changes or production traffic
are needed for these tests. Real production availability remains unverified.
