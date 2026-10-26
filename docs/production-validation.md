# Production preparation validation — 2026-10-26

This record supplements the historical 2026-10-23 validation. During implementation no Greenbone
stack, target scan, host installation, live deployment or remote push was performed.
The starting working tree was clean. AGENTS.md and CLAUDE.md were not edited;
CLAUDE.md's Bash-only/no-tests description is historical and does not match the
current Python maintenance engine or its stdlib tests.

## Implemented and verified in code

- Existing feed/application split, digest bundles, flock, strict scan gate,
  health checks and full/offline recovery layout are retained. All assigned task
  schedules still block maintenance; no task settings are changed.
- `doctor` reports actual DockerRootDir/volume Mountpoints and mount identities;
  optional DATA_MOUNT asserts placement. Protected Docker directories use a
  pinned, network-disabled read-only helper for apparent-size measurement.
- Operation-specific estimates sum extra bytes on each filesystem, preserve the
  MIN_FREE_MB floor and account for full image/volume archives, retained backups,
  temporary space, remote copies and old/new restore generations. Retention is
  never used to pre-delete the only good backup to make space.
- Optional expected remote mount/source assertions, pre-stop space/write probes,
  pinned directory descriptors for both probe/copy, checksum verification and
  target-replacement checks prevent a detached mount's local fallback directory
  from receiving the remote copy. Failures retain local backup and recovery state.
- Docker stop uses an infinite daemon grace period and bounded client wait.
  Expiry re-inspects state; PostgreSQL is never killed by a maintenance stop
  deadline. Normal stop keeps image/mount drift checks. Restore can replace an
  already crashed/stopped old container, but requires clean shutdown of any
  container that was running when stop started; old data is retained.
- Validated stop/backup/restore/image-archive budgets, blockwise budget checks,
  named disposable helper cleanup, explicit unknown image-operation completion,
  elapsed times and per-operation last-success timestamps were added. Resume
  records its own success, not a false successful backup/feed.
- Optional streaming gzip volume archives preserve plain-tar legacy reading,
  inventory/path/checksum validation and full offline image recovery. Docker
  image archives remain uncompressed; no compression savings are assumed by
  the space gate. Archive sizes are logged per volume and for the image set.

## Checks actually executed

| Check | Result |
| --- | --- |
| `python3 -m unittest discover -s tests -v` | **70 passed** (43 existing + 27 new) |
| `bash -n` on installer, uninstaller, entry point, tooling lock | Passed |
| ShellCheck 0.11.0 `-x` on those four files | Passed; existing binary used from /tmp, no host package installed |
| Python compile check for maintenance, GMP probe and Docker integration | Passed |
| `git diff --check` | Passed |
| Independent code review and focused re-review | Two concrete recovery/probe-race findings corrected and regression-tested; no unresolved findings reported |

Initial new regression tests failed against the old implementation before the
implementation was added. Tests cover shared-filesystem summation/reserve, missing
or incorrect mount/source, probe and mid-copy detach races, pre-stop remote/space
failures without retention, abnormal/slow shutdown, separate command budgets,
image-archive timeout diagnostics, gzip/legacy corruption and extraction, new
restore volumes, timestamps and unchanged feed/application behavior. Real native
`tar` subprocesses verify gzip/plain data and permissions in local unit tests.
Mount detachment is simulated by inode/directory replacement, not a real NFS
server failure.

Opt-in real Docker tests used only the cached `postgres:17-alpine` image. They
created random `gb-test-*` resources and cleaned their own containers/volumes/tags:

- **64 MiB, plain tar:** passed real database snapshot, image save/load, SQL and
  settings recovery into new volumes, smart shutdown with an active client and
  one-second stop client timeout, and timed-out import-helper cleanup.
- **1,024 MiB, gzip:** passed all of the above plus actual space inventory and
  compressed restore. The synthetic payload disables PostgreSQL TOAST compression;
  the repeated test values still compress very well in gzip. It is not a realistic
  report-entropy benchmark. In this run, image archive was 117,873,664 bytes,
  compressed full backup about 166.7 MB, snapshot/verify about 35.5 seconds and
  restore about 32.1 seconds. These local measurements are test evidence only,
  not production capacity or compression-ratio promises.
- **64 MiB, gzip, final implementation:** passed after the normal-stop drift gate
  and monitoring/recovery changes; fresh image save/load, graceful timeout,
  helper cleanup, new-generation restore and integrity all passed.

The initial Docker run exposed protected host volume paths under a non-root
maintenance identity. That was fixed by read-only pinned helper measurement plus
mount-table inspection, then exercised in the 1 GiB/final Docker tests. No live
Greenbone image/feed set was downloaded. GMP, compatibility and Greenbone startup
in these PostgreSQL tests remain explicit stand-ins; database/tar/image/volume
operations and capacity inventory are real.

## Still required on the intended VM

- Identify the selected scanner digest/binary version and its matching official
  source tag; verify actual task preference overrides and observed concurrency.
- Pilot 50–100 representative authorized Windows/Linux/network devices; measure
  completed hosts/hour, reachability/authentication, time/resource/I/O pressure,
  report/DB growth, feed loading and every maintenance stage. Demonstrate a
  batch/window plan for 4,000 hosts/month, with retries and headroom.
- Verify SSD mount persistence/source, Docker data-root and actual volume paths,
  filesystem/inode/quota/thin-pool headroom and restore-generation peak usage.
- Test the actual backup filesystem/server's disconnect, read-only, full-space,
  reconnect, fsync and kernel I/O timeout behavior. Mock detach tests do not
  certify NFS/CIFS/server durability or availability.
- Perform [real Greenbone acceptance](greenbone-acceptance.md): bootstrap → loaded
  feed health → authorized lab scan/report → feed/application identity comparison
  → complete backup → isolated new-volume/offline restore → task/report/settings
  integrity → successful authorized rescan. Clean replacement Docker-store
  recovery remains a separate part of that drill.
- Exercise data sizes representative of the intended retention horizon. A 1 GiB
  synthetic dataset is larger than the old smoke test, not a 500 GB stress test.
  No real Greenbone upgrade/schema migration was run or certified.

## Operator-owned controls

Choose/expand VM resources from pilot measurements; enforce scan admission across
web/API, direct GMP, internal schedules, dispatcher and Docker access. Arrange a
measured maintenance interval and non-overlapping/retried daily sequence without
bypassing locks or gates. Monitor nonzero/skipped work, last-success timestamps,
actual feed age and disk growth. Choose local/external retention and report-history
policy from actual sizes/RPO/RTO, protect an independent good backup, and approve
any report or old-volume deletion explicitly. Disk formatting, Docker data moves,
mount provisioning and deployment remain separate operator actions.

## Trail check rationale

After saving the new team learning, the unstaged check reported **seven lexical
conflicts** against the earlier and new learnings: Docker/network words
in the read-only volume measurement, scanner/binary wording in doctor, and
command/failure/data wording in the injected import-failure test. Four additional
word matches concern the documented schedule/window retry procedure, the negative
Docker stop timeout setting, the already-pinned write probe, and the intentional
slow-stop regression test. The time gate remains enforced; daemon stop timeout
is -1; both probe and copy are pinned. These do not
restore the ruled-out behaviors. The helper uses cached/pinned images,
`--pull=never`, `--network none` and read-only mounts. Scanner defaults/version
queries do not change tasks, bypass scan admission or replace functional health.
All command failures propagate and restore uses saved data/images in new volumes.
The earlier bandwidth note explicitly allowed necessary Docker tests; no new
images were downloaded here. The new learning records these decisions. There is
no clean Trail-policy-check claim or waiver. Carry this rationale into any later
commit/PR if the same lexical matches remain. The operator subsequently explicitly authorized committing and pushing this
completed change to main. No PR is required; the commit carries this rationale.

After all new files were staged for the authorized publication, Trail reported
20 lexical matches, including documentation of the rejected mechanisms and the
previous publication-phase note. These add no runtime bypasses or downloads.
The matching text describes authorized lab-only procedures, regression tests
and explicit limitations; it does not execute Greenbone deployment/scanning.
The commit carries the rationale for proceeding without claiming a clean check.
