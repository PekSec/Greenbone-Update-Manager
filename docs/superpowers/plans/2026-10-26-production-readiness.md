Production-readiness implementation plan
1. Keep the existing command flow, digest/feed policy, lock, scan gate and cold restore protocol.
2. Extend lib/maintenance.py and config/update.env.example with validated stop/archive/backup/restore budgets, read-only filesystem/volume inventory, grouped incremental space estimates, and optional mount/source identity assertions. Test missing mounts, low space shared across paths, no pre-deletion, slow stop and archive/restore failures before integrating callers.
3. Use Docker stop --timeout=-1 with a bounded client wait; inspect daemon/container state after failures and retain recovery state. Reuse in restore before removing only stopped containers. Preserve old volumes.
4. Add optional gzip volume streaming with legacy tar reading, checksum/inventory validation and restore tests. Report complete snapshot component sizes and timing.
5. Document 4,000 monthly hosts, representative 50–100 host pilot, actual scanner-version verification, disk layout, sizing/retention calculations, maintenance admission/retry monitoring and an explicitly opt-in real Greenbone acceptance runbook. No installation or scanning in this task.
6. Run unit tests, shell syntax, ShellCheck, available Docker plumbing checks, review changes, trail build/check, and save team learning. No push.
