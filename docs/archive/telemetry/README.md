# Historical only — retired custom telemetry

**These are not current installation instructions.** The feature and all
installable source were retired on 2026-11-13. Commands, acceptance limits,
DRAFT/OPEN labels, host observations and hashes below describe their original
historical scope, not today's deployment or permission to rerun a command.

Use the [current retirement checklist](../../telemetry-retirement.md) instead.
Time synchronization and `ops/time/chrony.conf` remain outside this retirement;
the clock-recovery report below is preserved evidence, not a chrony rollback.

## Provenance and reading old links

The documents and evidence JSON are byte-for-byte copies from commit
`9c1d08a6c5fee2925fcfbbd720321df7df9ac127`. Their original paths are retained inside this archive, and
[manifest.json](manifest.json) pins every content SHA-256. No implementation,
installer, service unit, shell shim, module, executable fixture or live host
configuration is installed from this archive. JSON manifests here are historical
records, not valid input for a new operation.

Some original relative links target code/configuration that is deliberately no
longer present, or the old root README. Do not repair the historical documents or
copy executable source back into this archive to make those links work. Open the
**immutable original** in the table for its complete original Git context. All
original implementation and retired tests also remain in Git at that commit.

## Preserved records

| Original path / unchanged local copy | Immutable original |
|---|---|
| [docs/operations-history-2026-10.md](docs/operations-history-2026-10.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/docs/operations-history-2026-10.md) |
| [docs/private-diagnostics.md](docs/private-diagnostics.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/docs/private-diagnostics.md) |
| [docs/rpc-exclusion-candidate.md](docs/rpc-exclusion-candidate.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/docs/rpc-exclusion-candidate.md) |
| [docs/telemetry-clock-recovery-2026-11-03.md](docs/telemetry-clock-recovery-2026-11-03.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/docs/telemetry-clock-recovery-2026-11-03.md) |
| [docs/telemetry-contract.md](docs/telemetry-contract.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/docs/telemetry-contract.md) |
| [docs/telemetry-operations.md](docs/telemetry-operations.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/docs/telemetry-operations.md) |
| [ops/telemetry/http-reexec/INCIDENT-RECOVERY.md](ops/telemetry/http-reexec/INCIDENT-RECOVERY.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/INCIDENT-RECOVERY.md) |
| [ops/telemetry/http-reexec/PR46-P2-EVIDENCE.md](ops/telemetry/http-reexec/PR46-P2-EVIDENCE.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/PR46-P2-EVIDENCE.md) |
| [ops/telemetry/http-reexec/README.md](ops/telemetry/http-reexec/README.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/README.md) |
| [ops/telemetry/http-reexec/ROOTSTAGING.md](ops/telemetry/http-reexec/ROOTSTAGING.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/ROOTSTAGING.md) |
| [ops/telemetry/http-reexec/host-baselines.json](ops/telemetry/http-reexec/host-baselines.json) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/host-baselines.json) |
| [ops/telemetry/http-reexec/host-readonly-configs.json](ops/telemetry/http-reexec/host-readonly-configs.json) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/host-readonly-configs.json) |
| [ops/telemetry/http-reexec/incident-runtime-evidence.json](ops/telemetry/http-reexec/incident-runtime-evidence.json) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/incident-runtime-evidence.json) |
| [ops/telemetry/http-reexec/review-manifest.json](ops/telemetry/http-reexec/review-manifest.json) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/review-manifest.json) |
| [ops/telemetry/http-reexec/root-staging-manifest.json](ops/telemetry/http-reexec/root-staging-manifest.json) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/http-reexec/root-staging-manifest.json) |
| [ops/telemetry/nginx-live/README.md](ops/telemetry/nginx-live/README.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/ops/telemetry/nginx-live/README.md) |
| [telemetry/HOLDER_RECOVERY.md](telemetry/HOLDER_RECOVERY.md) | [Git blob](https://github.com/me0kmin27/Keimirror/blob/9c1d08a6c5fee2925fcfbbd720321df7df9ac127/telemetry/HOLDER_RECOVERY.md) |

The October operations summary and November 3 clock recovery establish different
dated observations. Neither supersedes the need for a fresh host inventory.
In particular, the re-exec incident's expired failed journal, later recovery,
Nchan exclusion and incomplete large-worker testing remain truthful historical
records; retirement is not retroactive runtime acceptance.
