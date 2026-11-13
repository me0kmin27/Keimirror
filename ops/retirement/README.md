# Guarded runtime retirement (local execution only)

`retire_runtime.py` disconnects the inventoried custom telemetry without removing
mirror data, killing sync jobs, changing schedules, upgrading packages, or
restarting nginx/rsync. This is an operator tool, **not a deployment script**.
Use only after independent review of this code and a fresh private host plan.
Python 3.10+; Linux/systemd; standard library only.

## Exact supported scope

| Plan role | Exact hostname | Dedicated units |
| --- | --- | --- |
| `homepage` | `keiminem` | `keimirror-status-aggregate.service`, `.timer` |
| `mirror1` | `mirror` | collector/sizes services and timers, RPC helper service |
| `mirror2` | `mirror2` | collector/sizes services and timers; **not** the legacy agent |

Collector/sizes names are `keimirror-status-collector.{service,timer}` and
`keimirror-status-sizes.{service,timer}`. RPC is `keimirror-rpc-helper.service`.

Homepage changes exactly two files: canonical
`/etc/nginx/sites-available/keiverse` and the telemetry location snippet. The
`sites-enabled/keiverse` symlink is protected, not replaced. `/srv/keiverse` and
its ownership are untouched.

Mirrors change exactly the 13 scripts named in `SCRIPTS`, the two old telemetry
binary paths, canonical `/etc/nginx/sites-available/theme`, and these five files:

- `/etc/nginx/conf.d/keimirror-live.conf`
- `/etc/nginx/conf.d/keimirror-telemetry.conf`
- `/etc/nginx/modules-enabled/90-keimirror-live.conf`
- `/etc/nginx/snippets/keimirror-telemetry-location.conf`
- `/usr/lib/systemd/system/nginx.service.d/keimirror-live.conf`

The enabled-site symlink is protected. Only the single standalone
`include /etc/nginx/snippets/keimirror-telemetry-location.conf;` line is removed
from the canonical site; other bytes are preserved. Nonstandard formatting,
duplicate includes, symlinked changed files, hardlinks, and unexpected script
markers are refused rather than generalized.

Script transforms invert the four historical adapter forms:

- Generic `RSYNC="/opt/keimirror-telemetry/bin/rsync[-ssl]"` becomes the matching
  `/usr/bin/rsync[-ssl]` assignment.
- Arch restores `cmd=(rsync-ssl --type=openssl)` and `cmd=(rsync)` (bare names).
- OPNsense restores **`RSYNC_BIN="$(command -v rsync || true)"`** and leaves its
  `[[ -z "${RSYNC_BIN}" ]] && RSYNC_BIN="rsync"` fallback unchanged.
- Kali images restores the bare `rsync $RSYNC_OPTIONS $RSYNC_URL $TO
  >"$LOG_FILE" 2>"$ERROR_FILE"` command, preserving surrounding indentation.

All other bytes remain identical; scripts are syntax-checked, never executed.
Native ftpsync is protected and never adapted. Old `bin/rsync` and `bin/rsync-ssl`
become pure symlinks to verified `/usr/bin/rsync` and `/usr/bin/rsync-ssl` **after**
all scripts are restored. Existing observers/native children finish naturally.
No lock file is opened or deleted by this tool.

## Private plan contract

Supply a root-owned **0600 regular JSON file** outside webroots. Do not commit
it or its backups. Do not put file contents, credentials, environment dumps, or
private telemetry evidence in source control. Capture fresh facts on the host;
the tool does not generate approvals or invent missing hashes/boot IDs.

Top-level fields:

- `version`: integer `1`.
- `role`, `hostname`: one row above.
- `boot_id`: exact current `/proc/sys/kernel/random/boot_id` value.
- `backup_dir`: a new, nonexistent
  `/var/backups/keimirror-retirement/<letters-digits-underscore-hyphen>` directory.
  Its existing parent must be root-owned **0700**, with no symlink ancestors.
- `changes`: the complete exact allowlist for the role, in reviewed order.
- `protected`: independent complete inventory of unchanged sensitive/config paths.
- `units`: object mapping **every and only** dedicated unit for this role to
  `{"active": "inactive", "enabled": "static"}` or its exact fresh values.
  Supported operational cases are active/inactive/failed and
  enabled/disabled/static. Do not use masked, linked, generated, or runtime-only
  enablement without separate review.

Each file record has `path` (literal normalized absolute path), `kind: "file"`,
`sha256` (64 lowercase hexadecimal characters), numeric `uid`, `gid`, and `mode`
(POSIX permission bits; decimal in JSON). Each `changes` record additionally has
`operation`: `script`, `native-link`, `include`, or `remove` as described above.
Every changed file must be a canonical regular file with one hardlink.

For protected symlinks, use `kind: "symlink"`, `target` containing the exact
`readlink` text, and hash the **symlink text bytes**, not the target's contents.
Capture symlink uid/gid/mode with **lstat**. Include its canonical target as a
separate record when it is not already a changed file. The enabled-site symlink
must resolve lexically to the exact canonical site being changed.

The operator must inventory **all** SSH host/key/authorized_keys/known_hosts,
cron files and crontabs, native ftpsync, nginx binary/config/rules/unit fragments
and unrelated drop-ins, native rsync binaries/daemon config, telemetry unit
fragments, and package-owned keimirror-live module/shared includes as protected.
Mirror `observe.py` must also be protected. The utility enforces a mandatory
minimum subset, not exhaustive host discovery; **independent plan review is
required to establish full inventory coverage**. Protect corresponding symlink
targets explicitly. Do not hash live mutable observer state as immutable input.

The native binary hashes must come from independently verified installed native
binaries. Existing unit definitions/ExecReload and all drop-ins must be reviewed:
`systemctl reload nginx.service` must be the known graceful nginx reload, never
an upgrade/reexec/restart hook. Systemd manages enablement symlinks; these are
covered by recorded unit states, not individual file backup records.

## Usage

On the intended host, after separately authorizing and preparing the private
plan and root-only backup parent:

```sh
sudo /usr/bin/python3 -I -B ops/retirement/retire_runtime.py check --plan /root/retirement-plan.json
sudo /usr/bin/python3 -I -B ops/retirement/retire_runtime.py apply --plan /root/retirement-plan.json
sudo /usr/bin/python3 -I -B ops/retirement/retire_runtime.py verify --plan /root/retirement-plan.json
sudo /usr/bin/python3 -I -B ops/retirement/retire_runtime.py rollback --plan /root/retirement-plan.json
```

Default mode is `check`. Keep the exact plan bytes unchanged for verify/rollback.
A repeated apply refuses the existing backup instead of overwriting it.
No host calls are made by the test suite. No command here authorizes execution
on production without the separate runtime approval.

Apply validates identity, expected file hashes/ownership/mode and unit states
before writing; stores original bytes/ownership/mode under a root-only backup;
records a private journal; stops timers before dedicated services; disables
previously enabled dedicated units; restores scripts and then native compatibility
symlinks; disconnects only allowlisted telemetry nginx files; daemon-reloads;
runs `nginx -t`; and requests a graceful nginx reload. It verifies file content,
metadata, protected paths, unit inactivity/disablement, and unchanged nginx and
native rsync daemon master PIDs before publishing `receipt.json`.

On failure after journal creation it attempts validated file restoration and
independent restoration of previously active units. Previously inactive/failed
services are **not** started. A pre-reload syntax failure does not trigger an
nginx reload during recovery. Errors are sanitized and a failed recovery has no
success receipt. Manual rollback first validates all backups and refuses unknown
concurrent edits before overwriting any target. Backups are retained for review.

## Deliberate limitations and required operational verification

- This tool neither removes code/state nor implements finalize. Status is
  `pending-observer-drain` while observers exist, otherwise
  `retained-needs-separate-review`. Static services remain static/inactive;
  timers and previously enabled dedicated services become disabled. This is not
  a claim that telemetry source files or packages were uninstalled.
- Shared `/usr/share/keimirror-live` files and the package-owned module remain
  dormant. No dpkg operation, nginx binary replacement, restart, USR2/WINCH,
  forced synchronization, observer kill, or lock deletion exists in this tool.
- Serialize executions and prohibit concurrent edits/package jobs. Atomic
  per-file rename plus durable backups is not a cross-file transaction and is
  not a hostile-local-user race-proof filesystem sandbox. Abrupt machine loss
  can require manual journal/backup recovery; there is no automatic resume.
- PID equality and a successful reload command do not prove new-worker health
  or public reachability. Independently verify the actual master identity,
  effective nginx configuration, HTTP(S) projects and ranges, and public rsync
  listings after apply. Public rsync names are `rsync.keiminem.com` and
  `rsync2.keiminem.com`, not the HTTP mirror hostnames.
- Observer census reads cmdline, never environ, and returns only a count. It is
  an instantaneous count of the installed absolute `observe.py` invocation,
  not a proof against a delayed old wrapper spawn. No code is deleted even at
  zero observers.
- A formerly active oneshot can finish during recovery; strict unit readback can
  report incomplete recovery rather than claiming it remains active. Preserve
  evidence and review instead of restarting native services to satisfy a check.
- ACLs, xattrs, capabilities, and timestamps are not restored; use only reviewed
  regular configuration/scripts whose required metadata is uid/gid/mode.

## Local tests

```sh
python -m unittest discover -s tests -p test_retire_runtime.py -v
python -O -m unittest discover -s tests -p test_retire_runtime.py -v
```

Tests use the real orchestrator and disposable files with only the system
boundary replaced. The suite covers byte-preserving native restoration, live
observer retention, protected SSH/cron readback, backup validation, drift,
pre-reload failure recovery, prior-unit-state rules, and native daemon PID checks.
