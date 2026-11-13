# PR46 independent-review P2 follow-up (source only)

Base: `f57339ae0a09efd1c21b43ff35a3104266c37ceb`.

## Changes and boundaries

- `children()` distinguishes observed child disappearance/list changes with
  `ProcessSnapshotChurn`. Master identity, child PID/start/ancestry, malformed
  input, permissions and other validation failures remain fatal. `ready()` and
  `drain()` retry only that typed exception under the existing deadline; there
  is no phase/budget reset. A successful predicate must still fit the deadline.
- Fixture cleanup attempts primary and `.oldbin` independently, retains known
  PID/start pairs, retries only typed PID publication churn (one second per
  path), aggregates errors and sends only graceful QUIT after PID/start,
  executable, root UID, master role and exclusive chroot verification. All
  signalled identities have a shared five-second exit-verification deadline.
  PID reuse cannot replace a retained identity. Incomplete cleanup raises;
  final result publication occurs only after successful cleanup.
- Review/root-staging manifests hash the actual source bytes. This is a source
  binding update, **not** a root-owned execution-stage rebind or approval.
  Historical runtime receipts and their source/harness bindings are unchanged.

## Executed nonprivileged evidence

The regressions were written and observed failing before each repair. A later
negative-control replay loaded the actual base transaction bytes from git and
extracted only the base cleanup callback (never `run()`). Results:

- Base actual `children()` → `Linux.ready()` and `Linux.drain()`: both failed
  with `ValueError('child inventory changed')` on the first snapshot.
- Base cleanup: first PID read error stopped before `.oldbin`; assertion failed.
- Current: both inventory probes acquired the second complete/empty snapshot;
  all 17 new regressions passed. Coverage includes child disappearance, master
  and worker start reuse, malformed PID, permissions, executable/cgroup/role
  failures, bounded retry and post-success deadline, independent cleanup,
  bounded publication churn, retained identity reuse refusal, chroot proof,
  aggregate errors and alive/unreadable absence failures. Process and signal
  boundaries are substituted; no privileged fixture ran.

Commands:

```sh
python3 -m unittest discover -s tests -p test_http_reexec_churn.py -v
python3 -m unittest discover -s tests -p test_http_reexec_cleanup.py -v
env -u LIVE_DEPLOYMENT_NGINX python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tools/build.py --check
git diff --check
```

Full suite: **313 tests in 75.146s, OK (3 skipped)**, UID 1000.
Generated HTML/runtime current; diff whitespace check passed.
Local replay/logs: scratch `pr46-p2-tdd-replay.py`,
`pr46-p2-tdd-replay.log`, `pr46-p2-full-suite.log`.

## Gates remain closed

Nchan production gate unchanged and CLOSED. No host20 apply, production action,
new privileged fixture, or signals to existing PIDs 2832335/2832375. Their
pending cleanup approval was neither replayed nor rerouted. Updated harness
has **not** been privileged-runtime exercised. Fresh CI and independent review
must bind the pushed source head; this note is not an independent approval.
