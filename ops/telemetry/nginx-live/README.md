# Project live HTTP counters — custom experimental module

Built and integration-tested against the **unchanged extracted Debian nginx
1.26.3-3+deb13u10 amd64 binary**, not a locally rebuilt nginx. No production
installation, restart, reload, traffic probe or package change was performed.
Independent review and an explicitly approved canary remain required. This is
custom code, not an official Debian/NGINX supported module.

## Exact private collector ABI (v1)

Only these top-level keys are emitted:

```json
{"schema_version":1,"epoch":"32 lowercase hexadecimal characters","workers":[1234],"repositories":{"archlinux":{"rx_bytes":0,"tx_bytes":0,"uncertain":0,"active_uncertain":0}}}
```

The example abbreviates `repositories`; actual responses always contain exactly:
`archlinux`, `artix`, `artix-iso`, `artix-weekly-iso`, `cachyos`, `endeavouros`,
`fyralabs`, `gentoo`, `gentoo-portage`, `kali`, `kali-images`, `manjaro`,
`opnsense`, `proxmox`. Each row has exactly the four unsigned integer fields
shown. There are no metadata keys. The random 128-bit epoch belongs to one
shared-memory configuration generation. Every reload allocates a new zone,
never reuses previous counters, and produces a new epoch.

`workers` contains ready numeric worker PIDs with a module heartbeat no more
than 2000 ms old. Workers register before accepting requests and deregister on
normal exit. Heartbeats run every 1000 ms, are cancelable, and do not keep
workers alive. Crashes/stops cannot silently establish coverage: stale slots
expire; before expiry they mismatch an independent process inventory. The
collector MUST verify the exact master/worker identity set using `/proc`,
including old/shutting-down workers, before accepting any interval. Module
registration alone CANNOT detect pre-module workers. During mixed-generation
reloads, missing old workers must invalidate the sample, never imply zero.

The parent reader owns source/master validation, `/proc` start identities,
monotonic timestamps, <=2-second adjacent intervals, epoch hashing, missing
worker handling and public aggregation. PID registrations are private-only.
At either endpoint, nonzero `active_uncertain` invalidates that repository;
an increase of `uncertain` invalidates the interval even if a short unsupported
request began and ended between polls. An epoch/worker-set change or counter
regression requires a fresh baseline. No completed-request log delta is used.

## Byte semantics and limits

* TX is the positive **actual `ngx_connection_t.sent` advancement** across
  `send_chain`/`send` returns, including progress before an error return. It is
  HTTP plaintext application output including response headers/framing, and
  compressed body bytes when gzip is active. TLS records, handshakes,
  retransmissions, TCP ACKs and remote application consumption are NOT counted.
* `send_chain` wraps the existing handler after HTTP parsing/TLS setup. An
  optional direct `send` wrapper catches direct output (e.g. interim responses);
  a nesting guard prevents double counting. The original return is preserved.
  Threaded sendfile progress is observed when nginx processes completion and
  advances `c->sent`, not when the thread merely starts or data enters a filter.
* RX counts `request_length` once at **POST_READ**, only for bodyless GET/HEAD
  HTTP/1.0 or HTTP/1.1 without buffered pipeline bytes. This point is before
  body reading/discarding. Bodyfilter `request_length` is deliberately unused:
  nginx can temporarily include future pipelined bytes there.
* Other methods, any chunked/nonzero-length body, discard paths, recognized
  pipeline requests, late/error-only registration, HTTP >=400 responses,
  internal redirects and send/connection errors mark the request uncertain.
  RX for unsupported requests stays uncounted; HTTP/1 TX still progresses.
  Request-pool cleanup decrements active uncertainty, never cumulative history.
* HTTP/2 and HTTP/3 requests are explicitly uncertain and are NOT assigned a
  connection send wrapper. H2 is exercised with real frames. H3's unsupported
  version branch is implemented, but QUIC/H3 transport was NOT exercised.
* Mapping uses nginx's **normalized initial first URI segment**, case-sensitive
  exact allowlist match. Unknown projects and disabled vhosts are excluded.
  Internal rewrites remain attributed to the initial project and invalidate its
  interval; filesystem paths, symlinks and filenames are never used to infer
  attribution. Subrequest output belongs to the main response/initial project.
* Pre-routing bytes from incomplete/malformed requests that never reach a
  recognizable request/header filter are outside this per-project ABI; it is
  not a complete NIC/socket RX counter. HTTP/0.9 is unsupported. Do not label
  these values all network traffic or end-client consumption behind a proxy.
* Allocation/association failures fail closed by permanently raising active
  uncertainty for the generation. Worker death during uncertain activity can
  leave conservative active uncertainty until the next generation.

Connections are found through a worker-local rbtree keyed by connection
identity, with connection-pool cleanup. `c->data` is NEVER repurposed. Only
numeric repository IDs/counters and transient request pointers are retained;
no client IP, request header, URL/path/query, filename or payload is stored in
module state or emitted. Keepalive request cleanup removes the association
before the next project attaches. Shared counters/status snapshots use nginx's
shared-memory mutex; this implementation favors simple consistency over
lock-free scalability. Representative production concurrency overhead remains
unmeasured and is a canary acceptance item.

## Configuration (parent owns deployment)

`example.conf` is illustrative, not installed. Load the module at main scope;
put one `project_live_zone;` in `http`; put `project_live on;` only in each
approved mirror server. It is off by default and not a location directive.
Create `/run/keimirror-live` **root-owned mode 0700** before nginx starts.
The nginx master creates the Unix listener; workers inherit the open listener.
A privileged local collector connects to `/run/keimirror-live/status.sock`.
The status handler rejects every TCP listener, even if configured accidentally,
and rejects use in an enabled mirror server. Never proxy this endpoint publicly.
No performance directives (sendfile/AIO/gzip/cache/worker count) need changing.

The handler returns fixed Content-Length JSON and fits the parent's 32768-byte
body bound, even with 1024 PID slots. Parent HTTP reads have a total .3-second
budget; lock contention/frozen workers/read failure must invalidate, not become
zero. No read modifies traffic counters.

## Build and reproduce (off-host only)

```sh
python3 ops/telemetry/nginx-live/build.py /absolute/private/evidence/http-module
```

Requires Debian 13 amd64, existing cc/make, dpkg-source/dpkg-deb, apt, OpenSSL
headers/CLI, Python stdlib. It checks prerequisites first, downloads and extracts
stable `libpcre2-dev` and `zlib1g-dev` locally, and installs NO packages. It
fetches the official Debian-security orig tarball/DSC/debian tarball and applies
all Debian patches with dpkg-source. DSC component sizes/SHA256 are verified.
The local Debian maintainer signer key was unavailable, so the DSC signature
was not independently authenticated; HTTPS archive retrieval and component
hashes are recorded rather than falsely claiming signature verification.
The binary's SHA256 MUST match the previously audited origin hash:

`3f804ae7dda33a8368993d5d6087bfbc5fbf8c8ed1760af12fc9e8a74966d8a7`

Only `make modules` is run. Compatibility is exercised with `nginx -t` and
actual processes using the exact extracted Debian binary. The generated `.deb`
contains the module, normal package-owned load/http/server snippets under
`/usr/share/keimirror-live/`, and a runtime-directory-only systemd drop-in at
`/usr/lib/systemd/system/nginx.service.d/keimirror-live.conf`, with dependencies on **both**
`nginx (= 1.26.3-3+deb13u10)` and `nginx-abi-1.26.3-1`. No maintainer script,
auto-enable file, installation, package hold, production reload or commit.
Rebuild/review/retest on an nginx package change. Dependency resolution can
remove this custom package to permit an nginx upgrade. Parent deployment owns
stable `/etc/nginx/modules-enabled/90-keimirror-live.conf` and
`/etc/nginx/conf.d/keimirror-live.conf` indirection files, containing respectively
`include /usr/share/keimirror-live/load/*.conf;` and
`include /usr/share/keimirror-live/http/*.conf;`. The mirror server-scope include
uses `/usr/share/keimirror-live/server/*.conf`. Package removal removes the
module-dependent snippets (not conffiles); unmatched wildcards leave no dangling
module load or directives. Package installation alone does not enable the module.
The drop-in preserves nginx's ExecStart and creates the root-owned mode-0700
runtime directory at service start. Before the first graceful reload, the parent
must create `/run/keimirror-live` root-owned mode 0700 and run systemd daemon-reload;
daemon-reload alone does not create it. Do not hold OS packages to avoid rebuilding.

Evidence directory includes `.so`, `.deb`, exact extracted binary, source,
patches, configure/build/test logs, `live-progress.json`, and `manifest.json`
with actual SHA256 values. The deliberately broken `completion-only.so` is a
**negative-control artifact, NEVER deploy it**. Tests require its live-progress
assertion to fail, proving completed-request accounting cannot pass.

## Exercised coverage

* >10-second open response, positive adjacent TX samples, exact header RX,
  empty completion log throughout; completion-only mutant rejected.
* Live plain sendfile, AIO thread sendfile, OpenSSL TLS and dynamic gzip before
  request completion; memory output also tested.
* Full received raw HTTP plaintext byte equality against TX (headers and chunk
  framing included) for GET, HEAD, ranges/multipart and gzip, plain/TLS.
* All 14 projects concurrently, percent/dot/slash normalization, repeated
  keepalive project switching and connection/FD reuse.
* Unsupported POST, GET bodies, chunked/discard and same-read pipeline;
  cumulative uncertainty survives short requests; long active uncertainty.
* Partial abort of both supported and unsupported requests; early 400, 404 and
  internal rewrite; no cross-project count leak.
* Real HTTP/2 unsupported request; disabled-vhost/status exclusion; TCP denial.
* Exact strict JSON ABI; worker heartbeat stop/resume and reload epoch change.
* Deterministic C wrapper tests: partial progress followed by chain error,
  nested direct send without duplication, EAGAIN/no-progress adds zero.

Not established: H3/QUIC transport behavior, representative-load overhead,
production deployment/canary, every third-party module interaction, TLS egress
proxy accounting, exhaustive malformed pre-header ingress, or independent
kernel syscall trace reconciliation. Final byte tests independently compare
client-received plaintext, not fabricated socket traces.

## Primary lifecycle references

Review the fetched **Debian-patched** files under evidence `source/`:
`src/http/ngx_http_request.c` (request length and keepalive reset),
`src/http/ngx_http_request_body.c` (body/pipeline length corrections),
`src/http/ngx_http_write_filter_module.c` (`c->send_chain` and `c->sent`),
`src/os/unix/ngx_linux_sendfile_chain.c` (partial/thread completion),
`src/event/ngx_event_openssl.c` (SSL_write progress),
`src/core/ngx_palloc.c` (pool cleanup), `src/core/ngx_cycle.c` (`noreuse`).
Upstream development guide: https://nginx.org/en/docs/dev/development_guide.html
Stable source tag: https://github.com/nginx/nginx/tree/release-1.26.3
Debian archive: https://deb.debian.org/debian-security/pool/updates/main/n/nginx/
