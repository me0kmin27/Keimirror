# Inline mirror telemetry contract (v1)

## Scope

The existing ten homepage project rows display Mirror 1 and Mirror 2 telemetry directly. This is not a Grafana integration. Preserve project links, filters, locale/theme, previews, no-JS content and all existing access controls. No fake success, progress, size or bandwidth. Missing is `null`, not zero. Existing sync schedules, arguments, upstreams and SSH keys stay unchanged.

Project groups (repository IDs are a fixed allowlist):
- archlinux: archlinux
- gentoo: gentoo, gentoo-portage
- kali: kali, kali-images
- cachyos: cachyos
- artix: artix, artix-iso, artix-weekly-iso
- opnsense: opnsense
- endeavouros: endeavouros
- fyralabs: fyralabs
- manjaro: manjaro
- proxmox: proxmox

## Delivery

Each mirror targets an atomic, sanitized snapshot every second at `/var/lib/keimirror-telemetry/public/snapshot.json`, exposed read-only by its existing nginx as `/.well-known/keimirror-status.json`. No management tokens, internal paths, commands, client addresses, filenames or raw logs enter public data. Scheduling and collection can be delayed; verify actual sample intervals rather than equating a timer setting with achieved cadence.

A small independent aggregator on the homepage host fetches only these two fixed HTTPS endpoints each second with verified TLS, bounded body size/timeouts and no redirects. It writes `/var/lib/keimirror-telemetry/public/mirrors.json`, exposed on the homepage as `/status/mirrors.json`. Runtime data is outside the static deployment tree. The browser requests this same-origin JSON every second while visible without overlapping requests, never contacts Grafana or Termix, and preserves links if collection fails. This is short-interval sampling, not zero-latency streaming.

Aggregator JSON:
```json
{"schema_version":1,"generated_at":1793170800,"refresh_seconds":1,"mirrors":{"mirror1":{"available":true,"snapshot":{}},"mirror2":{"available":false,"snapshot":null}}}
```

`available` records whether the latest fetch/schema validation succeeded and the source remains recent. A retained older valid snapshot may accompany `available:false`; it MUST be shown as stale, never live. A snapshot older than 5 seconds or more than 1 second in the future is not live. Rates expire separately after 3 seconds. Browser stale detection also updates when requests fail; never leave an old syncing/completed state looking live. Keep the visible UI compact: browse link, state, total stored project size, RX/TX only. Timestamps and repository breakdowns remain API diagnostics, not extra visible paragraphs/disclosures. Do not announce all changing numbers via aria-live.

## Mirror snapshot

All timestamps are finite Unix seconds UTC or null. All bytes are nonnegative safe integers or null. Rates are finite nonnegative numbers in bytes/second or null. Mirror ID and all project/repository IDs are validated against fixed allowlists.

```json
{
 "schema_version":1,"mirror_id":"mirror1","collected_at":1793170800,"interval_seconds":1,
 "projects":{
  "archlinux":{
   "state":"unknown","last_attempt_at":null,"last_finished_at":null,"last_success_at":null,
   "exit_code":null,"warning":false,
   "size_bytes":null,"size_measured_at":null,
   "sync_rx_bps":null,"sync_tx_bps":null,"rates_observed_at":null,
   "http_bytes_60s":null,"rsync_bytes_60s":null,"traffic_observed_since":null,
   "repositories":[]
  }
 }
}
```

Every snapshot has exactly the ten project keys. Each `repositories` array has the group's exact known repository IDs. Repository objects have `id` plus the same metric fields as a project, without nested repositories. `state` is one of `syncing`, `completed`, `failed`, `unknown`. Public strings other than fixed IDs/states are not needed. Never expose exception messages.

### Semantics

- `syncing`: a current matching process (known script or rsync destination) or a verified live observer PID/starttime. Lockfile presence alone is insufficient. Actual running state wins over an old outcome.
- `completed`: authoritative last observed sync exit 0, or a specific existing completion contract (Kali ftpsync completion after stages; OPNsense successful canonical log). A generic `End:` line, wrapper script exit 0 after unconditional echo, directory mtime, HTTP 200 or Arch upstream timestamp is NOT success.
- `failed`: explicit current/last-run rsync failure, never collector unavailability. rsync 24 accepted by ftpsync can be completed with `warning:true`; do not silently call it a strict exit-0 success.
- `unknown`: no authoritative result, corrupt/missing source, interrupted observer with no terminal result, or incomplete evidence. Retain a separate prior `last_success_at` only if verified.
- Group state: syncing if any repository is active; otherwise failed if any is failed; otherwise unknown if any is unknown; otherwise completed. Retain repository details in the API for diagnosis without expanding the compact UI. Group success time is the oldest verified repository success, and null unless all are known. Group attempt/finish time is the latest known corresponding timestamp. Exit code is a nonzero failure code when applicable, otherwise 0 only for a verified complete group.
- `size_bytes`: GNU du allocated bytes, measured over the real repository directory (top-level symlink resolved by trusted configuration), deduplicating hardlinks within that repository. It is a non-atomic low-priority filesystem measurement, not rsync filelist size. No symlink traversal inside a tree. Cache measurement separately; do not walk trees on each one-second status tick. UI shows total stored project size, not disk capacity/quota; its title qualifies measurements older than 24 hours. Group size is sum only when every repository has a successful measurement, with oldest measurement time. Cross-repository shared inodes can be counted per repository; do not label the sum unique physical filesystem usage.
- `sync_rx_bps` / `sync_tx_bps`: optional sampled active upstream-sync TCP payload counters mapped through known job ancestry. Only compute deltas for the SAME socket and live process identity on consecutive approximately one-second samples. Use the actual elapsed interval, not a hardcoded denominator; reject gaps longer than 2 seconds. No moving average, smoothing, or 15/60-second average is substituted. Clearly label this as observed sync connections, not all host traffic. Missing coverage is null. Never distribute NIC totals, or convert progress2 reconstructed bytes to network bytes.
- `http_bytes_60s`: response body bytes from completed origin HTTP requests in a trailing 60-second window, project allowlisted at nginx. Includes HTTP and HTTPS-origin delivery together; do not claim transport-scheme separation or live in-flight download speed.
- `rsync_bytes_60s`: numeric `%b` from completed rsync serving file records in the trailing observed window, scoped to its module. Includes inter-mirror replication; do not call it unique external visitors' traffic or NIC wire total.
- Delivery counters use an inode/offset incremental cursor, partial-line handling, bounded reads, rotation/truncation detection, and a collection-start time. Values remain null until a complete 60-second observation window is available; errors/gaps restart coverage. Never rescan full raw logs on each tick. IP/filename are discarded on the mirror.

## Internal observer and size files

`telemetry/observe.py` is called through root-owned executable shims named `rsync` and `rsync-ssl` under `/opt/keimirror-telemetry/bin/`. It delegates to the corresponding original `/usr/bin/` binary, preserving exact argv, stdin/stdout/stderr, exit status, environment, lock descriptors and signal forwarding. No added transfer flags. It derives the repository only from the final absolute destination argument beneath the configured mirror/data roots; unmatched invocations execute without recording. Telemetry write failure must not block or convert the transfer result.

Per-repository state path: `/var/lib/keimirror-telemetry/runs/<id>/state.json`. Parent directories are provisioned for that job's existing user, not made world-writable. State contains:
```json
{"schema_version":1,"repository":"archlinux","phase":"finished","pid":123,"start_ticks":12345,"boot_id":"...","started_at":1793170800,"finished_at":1793170810,"exit_code":0,"last_success_at":1793170810}
```
`phase` is running/finished. This is private local data, not copied wholesale to the browser. Observer updates are atomic; valid previous last_success is retained on later failure. Multi-stage Kali uses its authoritative ftpsync evidence and parent activity, not a single intermediate subprocess outcome. A bootstrap scan may use bounded existing explicit failure/completion records; absence of an error never implies success.

`telemetry/sizes.py` writes `/var/lib/keimirror-telemetry/sizes.json` atomically:
```json
{"schema_version":1,"repositories":{"archlinux":{"bytes":123456,"measured_at":1793170800,"error":false}}}
```
A failed/timed-out scan retains the previous bytes/timestamp with `error:true` (collector publishes the prior timestamp, never a fresh timestamp for old bytes). Sequential repositories, bounded subprocess duration, nice/idle I/O, no overlapping scans. Each measurement succeeds only with du exit 0 and a validated numeric result. CLI/config paths are injectable for isolated tests, but production trusted config/root-owned code fixes all paths.

## Implementation ownership

- Collector agent: `telemetry/collector.py`, `tests/test_telemetry_collector.py` only. Pure stdlib; CLI `--config FILE --state-dir DIR --output FILE`, one bounded collection tick. Parent owns config, scheduling, aggregator and installation. Avoid editing shared generated files.
- Observer/size agent: `telemetry/observe.py`, `telemetry/sizes.py`, `tests/test_telemetry_observe.py`, `tests/test_telemetry_sizes.py` only. Agree simple CLI/config with parent in final. Do not alter actual remote sync scripts; parent applies reviewed minimal adapters.
- Frontend agent: project component, CSS, app/i18n/new telemetry JS module, tools/build.py and related builder test, browser telemetry test, generated index/site.js. Own generated output during implementation. No backend, workflows, deployment or remote mutations.
- Parent: aggregator, fixed configs, systemd/nginx snippets, tested patch/adapters, docs, full integration, review, GitHub and deployment.

Tests must demonstrate RED before GREEN. Test malformed/missing/stale sources, zero/null distinctions, interrupted jobs, signals, logger failures, rotation and partial reads, schema rejection, both locales/themes and mobile layouts. No production failures injected, no sync runs forced merely to obtain a green status. Observe naturally scheduled executions.
