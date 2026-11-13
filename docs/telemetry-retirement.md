# Custom telemetry retirement — operator checklist

Source retirement date: **2026-11-13**. This replaces the old installation
runbooks. **No live cleanup or deployment is established by this document.**
Every checkbox below requires a new, host-specific receipt from the operator;
source tests, old manifests and a static-site upload cannot satisfy it.

The custom collectors, aggregation, size scanner, observer shims, RPC helper,
native HTTP counter module and re-exec tooling are no longer shipped. Do not
reinstall them from the [historical archive](archive/telemetry/README.md).
The previous implementation is available in the immutable Git tree
`9c1d08a6c5fee2925fcfbbd720321df7df9ac127` for review, not execution approval.

## Preserve the mirror, not the instrumentation

- Preserve native synchronization scripts, argv/options, upstreams, schedules,
  users, locks, exit semantics, data directories and served links. Never run a
  sync or recursive size scan to test this change.
- Preserve HTTP/HTTPS and rsync serving, origin access controls, TLS certificates,
  SSH keys and account authentication. No forced disconnection of downloads.
- Preserve chrony and `ops/time/chrony.conf`, the existing Grafana dashboard/link,
  unrelated monitoring, native rsync logs, NFS/RPC services and mounted storage.
  The telemetry RPC helper is **not** rpcbind, rpc.statd or an NFS service.
- No OS/package upgrades, nginx binary replacement, reboot, global PATH change,
  broad process kills, generic `keimirror*` deletion or stopped-service revival.
  No USR2, WINCH, re-exec installer or nginx restart is part of this retirement.

## 1. Fresh inventory and rollback boundary

- [ ] Identify the homepage host and **each** mirror independently. Take a fresh
  inventory of actual unit fragments/drop-ins, timer enablement, cron owners,
  script hashes/metadata, config includes, process identities and listeners.
  Repository config is historical: for example the old mirror2 collector used
  `/srv/data`, while later re-exec evidence recorded `/srv/mirrors` as its
  document root. Resolve actual links/mounts; neither path is a deletion target.
- [ ] Record nginx executable/module hashes, master/worker PID + start identity,
  service MainPID, module/package ownership and relevant includes before edits.
  The old hosts had different HTTP module bytes. Do not infer current ownership
  or bytes from a package version or an old host manifest.
- [ ] Save exact affected script/config/unit files with owner/group/mode and
  hashes in a private backup outside web roots; verify archive readability.
  Preserve existing incident journals and backups, including failed receipts.
  Never publish raw logs, credentials or private host inventories to this repo.
- [ ] Record pre-change HTTP/HTTPS bounded Range responses, rsync list-only
  access, sync schedulers, SSH public-key/config hashes and chrony state. Use
  immutable small download content; changing `lastupdate` files are not stable
  hash probes. Keep TLS verification and existing access restrictions intact.

## 2. Stop telemetry scheduling only

Inventory first, then stop/disable only the verified telemetry units:

| Host role | Timers | Services |
|---|---|---|
| Homepage | `keimirror-status-aggregate.timer` | `keimirror-status-aggregate.service` |
| Each mirror | `keimirror-status-collector.timer`, `keimirror-status-sizes.timer` | `keimirror-status-collector.service`, `keimirror-status-sizes.service`, `keimirror-rpc-helper.service` |

- [ ] Stop timers before their oneshots, and confirm no new activation or
  telemetry `du` child remains. Inspect service cgroups before stopping them;
  native syncs/downloads must not be members of the deletion/stop set.
- [ ] Stop/disable the helper separately. Its unit only orders
  `Before=keimirror-status-collector.service`; the collector does not require it.
  `Restart=on-failure` and enablement must not bring it back.
- [ ] Read back active/enabled state and exact unit paths. Remove only their
  identified fragments, enablement links and telemetry-only overrides when
  rollback backups are verified; run systemd daemon-reload and read back again.
  Do not edit unrelated overrides or reset unrelated failed units.
- [ ] If a legacy `keimirror-agent.service` exists, establish its identity and
  use before classifying it as obsolete. Do not activate it or revive a stopped
  mirror-management service to substitute for this feature.

## 3. Restore executable tokens before removing the observer

Both historical mirror configs named these jobs; current paths, hashes, owners
and execution environments must be verified, not inferred:

| Job family | Historical script | Original executable expression |
|---|---|---|
| Arch | `/home/me0kmin27/script/arch-ssl.sh` | `cmd=(rsync)` and `cmd=(rsync-ssl --type=openssl)` |
| Generic | `/home/me0kmin27/script/{artix,artix-iso,artix-weekly-iso,cachyos,endeavouros,fyralabs,gentoo,gentoo-portage,manjaro,proxmox}.sh` | `RSYNC="/usr/bin/rsync"` or `RSYNC="/usr/bin/rsync-ssl"`, as proven for that script |
| OPNsense | `/home/me0kmin27/script/opnsense.sh` | `RSYNC_BIN="$(command -v rsync || true)"`, retaining its original `rsync` fallback |
| Kali images | `/home/archvsync/bin/mirror-kali-images` | `rsync $RSYNC_OPTIONS $RSYNC_URL $TO >"$LOG_FILE" 2>"$ERROR_FILE"` |
| Kali archive | `/home/archvsync/bin/ftpsync` | **Unchanged** by the old adapter; preserve upstream ftpsync |

The old adapter changed only executable tokens to
`/opt/keimirror-telemetry/bin/rsync` or `rsync-ssl`. It did not make those original
scripts, schedules, logs or locks telemetry-owned. The shims depended on
`observe.py`, `/etc/keimirror-telemetry/config.json` and
`/var/lib/keimirror-telemetry/runs/`.

- [ ] Validate per-script baseline hashes and the exact expected token count.
  Prefer the proven pre-adapter expression; compare with a private original
  backup, but never overwrite later unrelated script changes with a whole old
  file. Reject ambiguous or unexpected variants rather than broad replacement.
- [ ] Preserve every byte outside the verified executable expression, including
  quoting, `--type=openssl`, redirects, line endings, options and lock handling.
  Verify native executable resolution in the job's actual PATH/environment.
- [ ] Parse the candidate using its shell's `-n` mode (never source/run the sync),
  atomically replace it with original ownership/mode, and read back content and
  metadata. Compare cron/timer schedules and lock configuration unchanged.
- [ ] Search relevant actual cron/systemd/script references for both shim paths
  and direct `observe.py` calls. Retain the installed observer/config/state until
  every pre-edit shell/observer/transfer that may use it finishes naturally.
  A running shell may still hold the old script inode or an already assigned
  shim variable. No forced job cancellation or fabricated completion record.
  If safe draining is incomplete, report that residual instead of deleting it.

## 4. Remove telemetry nginx dependencies as one reviewed change

These are historical names, not a blind deletion glob:

| Concern | Configuration/dependency to locate |
|---|---|
| Homepage JSON | Exact `/status/mirrors.json` location, alias `/var/lib/keimirror-telemetry/public/mirrors.json` |
| Mirror JSON | Exact `/.well-known/keimirror-status.json` location, alias `/var/lib/keimirror-telemetry/public/snapshot.json` |
| Completion log | `$keimirror_project`/`$keimirror_repository` map, `log_format keimirror_telemetry`, its additional `access_log /var/log/nginx/keimirror-telemetry.log ...` |
| Mirror counter enablement | `include /usr/share/keimirror-live/server/*.conf;` / `project_live on;` |
| HTTP counter zone/private server | `/etc/nginx/conf.d/keimirror-live.conf` indirection, `/usr/share/keimirror-live/http/project-live.conf`, `project_live_zone`, `project_live off`, `project_live_status`, Unix listener `/run/keimirror-live/status.sock` |
| Module loading | `/etc/nginx/modules-enabled/90-keimirror-live.conf` indirection and `/usr/share/keimirror-live/load/project-live.conf` (possibly a versioned module target) |
| Runtime directory | `/usr/lib/systemd/system/nginx.service.d/keimirror-live.conf`, setting only `RuntimeDirectory=keimirror-live` and mode `0700`; discover any operator override |

- [ ] Inspect the effective include graph, including locally named JSON/log
  snippets; the original source does not establish every installed filename.
  Remove only telemetry locations, map/log directives, counter server/includes
  and load directive. Remove dependent directives **together**, not a module
  file first. Preserve other access logs, maps, security rules and modules.
- [ ] Review a minimal config diff; run **`nginx -t`** on the complete candidate.
  If it fails, restore the candidate files before attempting a reload.
- [ ] Use a **graceful reload**, retain the same master identity and check new
  workers, error log, listeners, actual HTTP/HTTPS ranges and rsync availability.
  Let old workers/downloads drain naturally; do not add a shutdown timeout or
  use signals that force termination. A successful syntax test alone is not
  runtime acceptance. Stop progression and preserve diagnostics on any failure.
- [ ] Verify the new configuration no longer enables counters or publishes
  status JSON, and the private status listener is no longer serving after
  natural drain. Old worker FDs or mapped module bytes can remain while draining;
  inspect actual mappings/holders and report residuals honestly. Do not claim
  every mapping is gone merely because the config or file was removed, and do
  not restart/re-exec nginx just to remove a harmless residual mapping.

### Package-owned versus manually versioned module files

The old custom package was `libnginx-mod-http-project-live`, with an exact nginx
version and ABI dependency. It shipped the unversioned
`/usr/lib/nginx/modules/ngx_http_project_live_module.so`, the three `/usr/share`
snippets above and the nginx runtime-directory drop-in. The later re-exec
candidate used an additional content-addressed
`ngx_http_project_live_module-<sha256>.so` and replaced the load fragment while
leaving dpkg metadata unchanged. Inspect both, separately for each mirror.

- [ ] First remove effective config references and verify the reload. Keep
  byte-verified rollback copies of all affected module files.
- [ ] Before any package removal, inspect the **installed** package file list,
  maintainer scripts, dependency simulation and reverse dependencies. The old
  source package had no maintainer scripts; that is not proof about today's
  package. Proceed only if removal is limited to the telemetry package and will
  not restart nginx, remove unrelated modules or replace its binary. No autoremove.
- [ ] Remove only proven telemetry-owned, unreferenced manual artifacts. Retain
  any mapped artifact needed for active workers or rollback until safe; do not
  wildcard-delete nginx modules or private re-exec incident evidence. Remove the
  dedicated runtime drop-in only after dependencies are gone, daemon-reload,
  then verify the unit still retains all unrelated lifecycle settings.

## 5. Remove unreferenced telemetry state, not mirror content

- [ ] After all writers and references are gone, privately retain needed
  evidence, then remove only identified retired `/opt/keimirror-telemetry`,
  `/etc/keimirror-telemetry` and `/var/lib/keimirror-telemetry` content. The latter
  included `collector.json`, `diagnostics.json`, `sizes.json`, `runs/*/state.json`
  and `public/{snapshot,mirrors}.json`. Confirm boundaries, mounts and symlinks
  before touching any directory. Leave unrelated content alone.
- [ ] The helper's `/run/keimirror-rpc/rpc.sock` and private `sysfs` mount belonged
  to its service mount namespace. Verify exit, namespace cleanup and mounts
  before removing leftovers; never recursively delete a mounted sysfs tree or
  unmount the host's `/sys`/mirror data to remove this helper.
- [ ] Retire `/run/keimirror-live` only after listener/process references are gone.
  Retain telemetry logs as private evidence if needed; delete only proven
  dedicated telemetry logs/rotation rules, never shared nginx logrotate policy.
  `/var/log/rsyncd/transfer.raw.log` was read by the collector; that alone does not
  make it telemetry-owned or justify editing rsync daemon configuration.
- [ ] The homepage's `keimirror-status` account and sysusers entry are dedicated
  candidates, not an automatic user deletion. Verify no remaining consumers or
  owned files first. Preserve native mirror users and all SSH/account settings.

## 6. Final independent readback and rollback

- [ ] No telemetry timer/helper/collector/aggregate/size process or future shim
  reference remains; document any safely retained draining artifact separately.
- [ ] Public status paths no longer serve telemetry JSON (expect a deliberate
  not-found/gone response, not stale JSON or an HTML fallback counted as success).
  The retired browser runtime makes no status requests or telemetry timers.
- [ ] Verify the exact deployed static file hashes, both locales and desktop/
  mobile browsers; preserve 10 project rows, 20 direct mirror links, six protocol
  addresses, filters/search, themes, no-JS access, copying, preview and Grafana.
- [ ] Recheck bounded immutable-file HTTP/HTTPS Range content and rsync list-only
  service on both mirrors, native scheduler/script/lock preservation, chrony and
  SSH/access-control baselines. Do not equate these probes with full mirror
  freshness, complete data integrity or a completed native synchronization run.
- [ ] Record source review/CI, homepage deployment and each host cleanup as
  separate acceptance gates. A passing local suite verifies source only.

If a config/script change regresses serving or scheduling, restore only its
verified pre-change bytes and metadata; syntax-check before graceful reload and
read back the actual service. Do not kill active downloads, launch a sync, restore
an expired transaction journal as "successful", or automatically re-enable retired
timers as a repair. An ambiguous rollback or incomplete drain is a blocker to
further deletion and must be reported, not hidden by a new test receipt.
