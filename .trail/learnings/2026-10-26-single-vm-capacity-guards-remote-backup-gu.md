---
title: "Single VM capacity guards, remote backup identity and graceful recovery"
author: Güven
date: 2026-10-26
branch: main
touches:
  - README.md
  - config/update.env.example
  - lib/maintenance.py
  - tests/docker_integration.py
  - tests/test_workflows.py
  - docs/greenbone-acceptance.md
  - docs/production-readiness.md
  - docs/production-validation.md
  - docs/superpowers/plans/2026-10-26-production-readiness.md
  - tests/test_production.py
---

## Decided
Keep the existing feed/application digest split, shared lock, strict assigned-schedule scan gate and cold offline recovery. Monthly capacity target is 4,000 hosts, measured from a 50–100 representative host pilot; DefectDojo's 500,000 accumulated findings including Info are a different dataset. Initial 16 vCPU/32 GB/80 GB system plus expandable 500 GB SSD is a planning budget, never a capacity guarantee.
Operation estimates group extra bytes per filesystem before stop, retaining MIN_FREE_MB and old backups/volume generations. Optional DATA_MOUNT and remote mount/source assertions check actual placement. Pin directory descriptors for BOTH write probes and copy so a detached share never redirects writes into the local fallback. Optional gzip streams volume archives; legacy plain tar remains readable and images stay archived for offline recovery.
Use Docker stop --timeout -1 with a bounded client wait, then inspect daemon state after timeout. Normal maintenance retains clean exit and image/mount drift gates. Restore may replace containers already crashed/stopped before the attempt, but never accepts a newly unclean stop of running PostgreSQL. Resume gets its own timestamp and cannot mark a failed backup/feed successful.
## Tried and ruled out
Finite Docker stop/down timeouts can SIGKILL a large database. Applying the normal clean-backup exit gate blindly to preexisting crashed containers prevents disaster recovery. Pinning only copy leaves the preflight write probe vulnerable to detach; pin both. Protected Docker paths cannot be treated as zero-size: use the pinned read-only helper plus host mount-table identities. Do not pre-delete the only good backup or assume seven full snapshots fit 500 GB. Do not loosen the schedule gate or bypass the window to fix timer overlap.
## Watch out
70 unit tests, shell syntax and ShellCheck pass; cached PostgreSQL tests cover 64 MiB plain/gzip and 1 GiB gzip, real slow smart shutdown and timed-out import cleanup. These are stand-ins, not a real Greenbone scan/feed/upgrade or 500 GB stress test. Operator still owns mounts/admission/retention/monitoring and opt-in real Greenbone acceptance. Kernel network-filesystem stalls can exceed cooperative budgets; Docker image save/load completion can remain unknown after client timeout. Trail's lexical Docker/network, scanner/binary and command/failure matches do not reinstate old unsafe behavior; details are in docs/production-validation.md for any later commit/PR rationale.
