---
title: Controlled Greenbone maintenance and cold recovery
author: Güven
date: 2026-10-23
branch: main
touches:
  - AGENTS.md
  - CLAUDE.md
  - README.md
  - bin/greenbone-update.sh
  - config/update.env.example
  - cron/greenbone-update.cron.in
  - install.sh
  - logrotate/greenbone-update.in
  - systemd/greenbone-update.service.in
  - systemd/greenbone-update.timer.in
---

## Decided
The entry point requires an explicit command. Only the seven official feed services follow mutable data channels. Application/database/migrator/GPG/initializer images are kept in reviewed local digest bundles. All maintenance and tooling replacement share the runtime lock; incomplete operations block automated writes.
Backups are cold snapshots of all non-socket volumes plus exact image archives and private config/credentials. Restore requires project confirmation, verifies the backup first and uses new volumes with saved image content identities. There is no silent automatic data rollback.
## Tried and ruled out
The old pipeline lost command failures in a subshell, HTTP-only health missed scanner/feed failures, and restoring Compose alone could start old binaries against migrated data. Digest-only docker save arguments also fail to preserve registry digest associations on classic Docker; save protected tags and verify image content when loading.
## Watch out
External maintenance admission is mandatory: a local lock cannot atomically forbid new GSA/GMP scans. Any assigned task schedule or unknown scan state blocks maintenance. Check actual mounts and running image identity, preserve physical volume names across upgrades, and compare loaded feed database markers with deployed feed files. PostgreSQL integration is a stand-in plumbing drill; real Greenbone migration/feed behavior and capacity still require a VM pilot.
