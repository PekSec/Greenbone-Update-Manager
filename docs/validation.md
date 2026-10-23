# Validation and review record — 2026-10-23

The old timer deployed mutable application/database images, lost failures inside
its logging pipeline and treated copying Compose back as rollback. The replacement
requires explicit commands, isolates seven feed channels, records reviewed image
sets, takes cold volume/image backups and requires explicit recovery after a
failed migration. Nothing was installed as a host service or deployed as a
Greenbone stack during implementation. The operator subsequently authorized
committing and pushing the maintenance change to main. Pre-existing unrelated
working-tree changes remain outside that commit.

## Checks performed

- `python3 -m unittest discover -s tests -v`: **43 passed**. Coverage includes
  pull/deploy failures, lock conflicts, active/scheduled/unknown scans, GMP query
  failures and pagination, private config, no shell evaluation, incomplete
  operations, corrupted/missing backups, failed restoration, volume/image drift,
  preserved upgrade volumes, stale feed database markers, image archive identity,
  separate copies, retention anchors, resume, report-only version checks and
  installer dry-run/legacy timer migration/cron/uninstall behavior.
- `for script in install.sh uninstall.sh bin/greenbone-update.sh lib/tooling-lock.sh; do bash -n "$script" || exit; done`:
  passed. ShellCheck **0.11.0** with `-x` on the same files: passed.
- Official Compose downloaded for inspection, normalized with Compose and accepted
  by the architecture validator. No full Greenbone image/feed set was pulled.
- Real cached `postgres:17-alpine` integration: stopped PostgreSQL and app settings
  volume snapshot, Docker image archive/load, all non-socket volume archives,
  checksums/private permissions, restoration into fresh volumes and SQL evidence
  of pre-migration content. Random `gb-test-*` resources are cleaned by the test.
- Actual official `gvm-tools` image: Python imports, socket constructor and
  authentication/task/config API signatures checked in a temporary isolated
  container without data mounts. The single tools image was downloaded; the
  PostgreSQL image was already cached. No production scan or Greenbone deployment.
- `git diff --check`: passed. Existing AGENTS.md and CLAUDE.md changes were preserved
  byte-for-byte relative to the state observed at task start.
- Trail graph rebuilt. `trail check` produced lexical warnings discussed below;
  it must not be reported as a clean policy check.

## What was not validated

No real Greenbone installation, upgrade/migration, scanner/feed import or full
GMP round trip was run. PostgreSQL is a deliberate stand-in for backup plumbing;
Greenbone compatibility and health in that integration test are mocked. The
real-image check confirms library availability/signatures, not live manager
behavior. A clean alternate/classic Docker image-store restore was not executed;
restoration uses archived tags plus verified image content IDs specifically to
avoid depending on registry digest associations surviving `docker load`.

A representative VM pilot, scan admission enforcement, actual feed schema/layout,
backup disk/share reliability, complete Greenbone restore and 2–3k-host capacity
remain deployment acceptance checks. Do not enable unattended jobs before these.

## Review rationale for Trail warnings (proposed PR description)

Trail's literal matcher initially flagged eight README/config/scheduler matches.
After staging the new source and test files it reported twenty matches, including
the phrase "not a blanket ban" as if it were a prohibition. These word matches
do not reinstate the old behaviors or execute the described commands:

1. The bandwidth preference governs agent-side inspection downloads. Documenting
   an operator's explicit `prepare`/`bootstrap` procedure does not execute it.
   Tests reused cached PostgreSQL, downloaded one tools image, and never pulled
   the complete application/feed set. Future automatic feed updates are the
   user's requested product behavior, not downloads performed during editing.
   Docker commands present in test source do not imply Docker execution during
   the subsequent commit/push step; only local unit/lint checks were repeated.
2. The old pipeline/HTTP-only-health/Compose-only-rollback mechanisms were
   removed. The Bash entry point uses `exec`; Python checks subprocess return
   codes; health checks query GMP plus the scanner/database/feed data; recovery
   restores a cold data/image set into new volumes. README references to these
   concepts explain the replacement and its limits.

Retain these requirements. Do not remove useful operator documentation merely to
silence a lexical checker. This rationale is ready to carry into a future commit
message or PR description; the commit carries this rationale. No PR was opened
and no clean-check result or waiver is claimed.

## Files changed by this task

- `.gitignore`
- `README.md`
- `bin/greenbone-update.sh`
- `config/update.env.example`
- `install.sh`, `uninstall.sh`
- `systemd/greenbone-update.service.in`, `systemd/greenbone-update.timer.in`
- `cron/greenbone-update.cron.in`, `logrotate/greenbone-update.in`
- `lib/maintenance.py`, `lib/gmp_probe.py`, `lib/tooling-lock.sh` (new)
- `tests/test_maintenance.py`, `tests/test_workflows.py`, `tests/test_gmp_probe.py`,
  `tests/test_installer.py`, `tests/docker_integration.py` (new)
- `docs/maintenance-design.md`, `docs/validation.md` (new)
- New team learnings in `.trail/learnings/`; pre-existing Trail setup preserved.

AGENTS.md, CLAUDE.md and the pre-existing untracked Trail setup were not authored
or reverted by this task. The bandwidth preference was saved personally, outside
Git, rather than added to repository configuration.

## First operator command

From the repository checkout:

```bash
./install.sh --user-mode --scheduler none --dry-run
```

This performs no deployment, scheduler changes or Docker image downloads.
Follow README for the actual VM installation and pilot after reviewing the diff.
