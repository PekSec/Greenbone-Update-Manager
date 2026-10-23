# Maintenance contract

The requested scope authorizes implementation in this checkout. Existing user
changes to AGENTS.md and CLAUDE.md are left intact. Nothing is installed on the
host. The operator subsequently authorized committing and pushing the completed
maintenance change to main. Bash remains the entry point and installer; Python 3 standard
library handles JSON, subprocess exit status, locking, archives and state. There
are no host pip packages. The official gvm-tools image supplies python-gvm.

## Decisions

- Use reviewed local release bundles, not a live upstream Compose URL at runtime.
  A release is an operator-named snapshot, source reference, Compose JSON and all
  image digests. Greenbone containers are a rolling set, not one product version.
- Only the seven officially listed feed services can change automatically.
  GPG keys, initializers, database, migrator, tools and application images are
  application components and remain pinned. Unknown service layouts fail closed.
- Restrict supported feed policy to the reviewed 24.10 feed and gvmd 25/26.
  This is a conservative local policy, not a claim that all component combinations
  are compatible. Operators approve the complete set against official references.
- All writes share one flock. A persistent incomplete-operation record blocks
  subsequent automated writes. Errors never trigger automatic data restoration.
- Check all tasks with a Super Admin GMP account. Active, queued, unfamiliar states
  or any assigned schedule block maintenance. Require an externally enforced
  maintenance window; the lock cannot stop UI/API clients or gvmd schedules.
  The operator closes web/API ingress; check twice, then stop the whole stack
  before changing data.
- Cold backup after all containers have stopped and no other running container
  mounts a volume. Include every non-socket volume and exact image archives.
  This includes re-downloadable feed/static/log data for a repeatable offline
  restore. It costs storage and downtime but avoids an online cross-volume
  consistency protocol. Live PostgreSQL filesystem copies are never accepted.
- Restore needs explicit project-name confirmation. Restore to newly named
  volumes; keep pre-restore volumes for investigation. Start the saved image set,
  not old images against a migrated database. No image/volume prune.
- Health includes every service's expected lifecycle, authenticated GMP, scanner
  verification, all four feed types and imported scan configs/port lists/formats.
  Healthy copying containers and successful exited initializers are both valid.

## Alternatives considered

Online pg_dump plus application-volume snapshots requires coordinated writers
and a restore protocol for the PostgreSQL major version. Cold volume snapshots
are simpler for this single-VM deployment. A larger orchestrator/Kubernetes is
unnecessary. Ad-hoc sed of upstream YAML and Bash parsing of GMP/XML are rejected
because the current failures involve precisely those boundaries.

## Implementation and checks

1. Write stdlib unit tests for configuration, subprocess failures, scan/feed
   gates, release validation, backup corruption, locks and incomplete operations;
   observe missing implementation fail, then implement lib/maintenance.py and
   lib/gmp_probe.py behind bin/greenbone-update.sh.
2. Update installer, config, systemd/cron and uninstaller together. Test dry-run
   and old scheduler removal with fake host commands. No implicit default update.
3. Run Bash syntax, ShellCheck, unit tests and isolated Docker cold-backup/restore
   integration. Document the distinction between plumbing tests and a full
   Greenbone pilot, plus first install, upgrades, migration and recovery.

Review focus: pagination/permissions, scan admission races, partial migration,
incomplete backup replication, archive corruption, and stop/restart failures.

## Official sources reviewed (2026-10-23)

- https://greenbone.github.io/docs/latest/22.4/container/index.html
- https://greenbone.github.io/docs/latest/22.4/container/workflows.html
- https://greenbone.github.io/docs/latest/changelog.html
- https://github.com/greenbone/greenbone-feed-sync#version
- https://docs.greenbone.net/API/GMP/gmp-22.7.html
- https://greenbone.github.io/python-gvm/usage.html

The upstream documentation explicitly calls its container guide a learning/test
setup. This repository does not certify production readiness or a 2–3k host
capacity. A representative pilot and restore drill remain mandatory.
