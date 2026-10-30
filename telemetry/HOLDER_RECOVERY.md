# Verified missing-ss-holder recovery

This is a local behavior repair, based on `afa3d18`, independent of HTTP-accounting changes. It is not a port, UID, executable-name or RPC-tuple exemption.

## Proof and failure boundary

Only active PID-less records with a joined INET_DIAG identity and transient endpoints are candidates. One lazy, bounded FD inventory is shared by every candidate in an observation attempt. Already populated ss ownership does not request this new inventory; independently proved kernel RPC exclusions retain their existing path.

The inventory must enumerate every process from the initial process table and every visible FD, without skipping failed reads. It rejects newly seen or disappeared processes, unreadable inventories, bounds and deadlines. All matching holders are retained. Each holder must match the initial PID/start/UID/argv/parent identity, be live, and belong to the observer's network namespace. After the scan, a fresh diagnostic dump must identify exactly one socket with the same inode, cookie, UID, state and endpoint. Holder identity, FD target and namespace are rechecked after that dump and again by the sampler's existing final verification. Ambiguous or reused inode/cookie identities never establish proof.

Recovered numeric PID/FD tuples enter the existing upstream and native-chroot delivery classifiers, including the existing verified root-to-nobody transition. A stable, classified unrelated holder can remove the empty-coverage veto. A mismatched UID, unclassified transport or inconsistent shared ownership cannot. Previously attributed cookies still invalidate their repositories when ownership is lost.

A recovery-specific permission/validation failure leaves the missing socket unknown while preserving independently known sockets. ENOENT restarts the entire observation (the existing maximum of three attempts); deadline exhaustion remains unknown. All readers retain the existing single two-second budget. Existing RPC proof acquisition and its scans are unchanged.

## Private numeric counters

- `36` (`TCP_HOLDER_RECOVERED_PROJECT`): a recovered socket successfully attributed to that project.
- `37` (`TCP_HOLDER_RECOVERED_UNRELATED`): a recovered socket classified unrelated to configured projects; counted for each configured project whose empty coverage it would otherwise veto.

These counts are emitted only after the attempt's final holder/process checks, through the existing bounded private recorder. They are recovery counts, **not a guarantee of a non-null project rate**: another socket, a previous-cookie loss, an invalid pair window or the outer deadline can still reject a rate. Existing reason counters accumulate across attempts. No socket identities, endpoints, argv, names or paths are added to the sidecar, public JSON or private rate cursor. No exact-event diagnostic schema extension was needed for the local behavior repair.

## Reproducible checks

```sh
python3 -m unittest tests.test_telemetry_holder_recovery -v
unshare -Urnmpf --mount-proc python3 tests/holder_recovery_runtime.py
python3 tools/build.py --check
python3 -m unittest discover -s tests -p 'test_*.py' -v
npm ci
npm test
```

The runtime fixture uses a disposable user/PID/network/mount namespace, real loopback TCP sockets, real `/proc` FDs and process identity, real INET_DIAG and clocks, and an intentionally owner-omitting ss fixture. It verifies adjacent observed-empty zero and rejection of a live-state transition. It does not use the production hosts or claim real nginx/rsync deployment acceptance; those classifier paths have isolated filesystem/process fixtures.

The original private diagnosis established independently observed root-nginx ss ownership gaps on both hosts, **not the identity of an exact past production veto**. Local tests do not establish that historical root cause, or that both production hosts now accept this code. No deployment, push, traffic generation or service change is part of this patch. Exact-source independent review and same-code host acceptance remain release gates.
