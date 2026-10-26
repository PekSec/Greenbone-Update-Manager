# Single-VM production preparation and capacity pilot

This is an operator procedure, not a measured capacity guarantee. Greenbone
Community runs on one Docker Compose VM; DefectDojo runs on another VM. The
initial Greenbone budget is **16 vCPU, 32 GB RAM, 80 GB system disk and a separate,
expandable 500 GB SSD data disk**, with an independent backup target. Neither
Kubernetes nor a distributed scanner is required by this design.

The target is **4,000 hosts per month**, in batches. Approximately **500,000
accumulated DefectDojo findings including Info** is a separate downstream data
model: deduplication, reimports, severities and retention change its count. It
is not Greenbone's report count or result count. Measure Greenbone database,
reports and full backup sizes independently; size DefectDojo on its own VM.

## Evidence and scanner settings

The checked-in adapter accepts feed release 24.10 and reviewed gvmd 25/26 sets.
The repository contains no deployed `current.json` or fixed scanner digest;
`prepare` resolves the operator's reviewed set. A gvmd major does **not** identify
the OpenVAS scanner version. Record the prepared bundle checksum, every image
reference/digest, image ID, Compose source commit and actual binary versions in
the pilot record. Never substitute today's `stable` documentation for evidence
about a different pinned binary.

Official references reviewed on 2026-10-26:

- [Community container topology](https://greenbone.github.io/docs/latest/22.4/container/index.html)
  and [feed workflow](https://greenbone.github.io/docs/latest/22.4/container/workflows.html#performing-a-feed-synchronization):
  `ospd-openvas` runs the scanner; `openvas` is the log viewer;
  `configure-openvas` writes `/mnt/openvas.conf` into `openvas_data_vol`, mounted
  as `/etc/openvas/openvas.conf` by the scanner. `openvasd` supplies Notus in this
  topology, rather than replacing the OSP scan path.
- [OpenVAS scanner manual](https://github.com/greenbone/openvas-scanner/blob/main/doc/man/openvas.8.in)
  and [scanner implementation](https://github.com/greenbone/openvas-scanner/blob/main/src/openvas.c):
  `max_hosts` limits simultaneously scanned hosts **within a scan**;
  `max_checks` limits concurrent VT checks **per host**. Their product is a
  rough VT worker budget, not hosts/month, a global cross-task admission limit,
  or a promise that that many checks will always run. Client scan preferences
  can override scanner configuration defaults. Multiple tasks multiply demand.
- [GMP task preferences](https://docs.greenbone.net/API/GMP/gmp-22.7.html#command_get_tasks)
  expose `scanner_name` values `max_hosts` and `max_checks`;
  [task UI controls](https://docs.greenbone.net/GSM-Manual/gos-24.10/en/scanning.html#creating-a-task)
  label them “Maximum concurrently scanned hosts” and “Maximum concurrently
  executed NVTs per host”. The appliance manual explains semantics, not Community
  hardware limits or a container sizing guarantee.

Run `doctor` on the intended VM. It executes `openvas --version` and `openvas -s`
inside the accepted `ospd-openvas` container, reporting the version and just
these two configuration defaults, without printing credentials or altering tasks.
If either setting is missing, it fails rather than guessing. With that actual
version, review the corresponding official scanner tag/commit manual and source
and retain its immutable URL alongside the release evidence. Inspect the
**pilot task's** exported GMP `<get_tasks task_id="PILOT_UUID" details="1"/>`
preferences as well. Scanner defaults alone do not prove effective task values.
Confirm the observed concurrency during the scan. Any disagreement is a failed
pilot prerequisite, not permission to silently patch configuration.

Use a **new explicitly named pilot task**, no assigned schedule, and start with
`max_hosts=5`, `max_checks=2`, one task at a time. These are cautious local trial
values, not official capacity recommendations; fragile network equipment or
compliance tests may require 1/1. Set them explicitly in that task's GSA controls
or reviewed GMP task preferences; the maintenance tool never edits existing task
settings. Do not edit the generated `openvas.conf` expecting it to override
per-task preferences or survive a rerun of `configure-openvas`. Any persistent
scanner-default change belongs in a newly reviewed Compose initializer/release.

## Representative pilot and capacity decision

1. Obtain authorization for an isolated pilot and select **50–100** representative
   Windows, Linux and network devices. Include actual WAN latency, port exposure,
   authenticated and unauthenticated profiles, large servers and slower devices.
   Validate DNS/routing, allowed ports and credential privileges first. Record
   profile, port list, alive test, QoD/severity/export filters and feed version.
2. Run at least three comparable batches at 5/2 with one task active. A “Done”
   task is insufficient: count distinct completed hosts, reachable hosts and
   hosts with successful authenticated local checks. Check authentication-result
   VTs/package evidence, not merely whether a credential was configured.
3. Capture this table per batch and by device class. Use existing VM monitoring,
   `docker stats --no-stream`, `vmstat`, `iostat -xz` if installed, `df -B1`,
   `docker system df -v`, PostgreSQL/volume size and GMP report/export evidence.
   These are read-only observations; do not install agents as part of this task.

| Metric | Measurement |
| --- | --- |
| Completed hosts/hour | Distinct fully completed hosts / elapsed scan wall hours; include retries separately |
| Reachability | Reached / intended hosts, with alive-test exclusions listed |
| Authentication | Successful local checks / intended authenticated hosts; failure reasons by OS |
| Scan duration | Queue delay, total wall time, per-host median/p95, slowest device |
| CPU/RAM | Host and scanner/DB peaks, sustained load, memory available, swap/OOM events |
| I/O | SSD latency/await, utilization, throughput, I/O wait and free bytes/inodes |
| Growth | DB/volume and unfiltered report XML bytes before/after; report/result counts including Info |
| Feed | Pull/copy time versus scanner/DB import-to-`doctor` time and actual feed timestamps |
| Maintenance | Stop, image save/load, volume archive, verification, separate copy, restore and health durations |

4. Agree local acceptance thresholds before running. Suggested trial guardrails:
   no OOM or sustained swapping, no target disruption, at least 20% RAM available,
   no sustained SSD saturation, successful expected authentication (investigate
   every failure), and completion plus maintenance comfortably inside the
   allocated window. These are tuning criteria, not vendor guarantees.
5. If three repetitions are stable and complete, change **one** dimension on
   the pilot task, e.g. hosts 5 → 8 → 10 while checks remain 2. Re-measure each
   step and revert if completion rate, target safety or tail latency worsens.
   Test increasing checks separately only if justified. Do not launch 4,000
   hosts concurrently or raise both values to hide a slow/failed scan.
6. Compute `required_rate = 4000 / usable_scan_hours_per_month`. For example,
   20 days × 6 scan hours = 120 hours means 33.3 completed hosts/hour **before**
   retries. Reserve a further 25–30% for unreachable/retry work and variation.
   Use the measured device mix's conservative rate; schedule batch counts and
   spillover days accordingly. If it does not fit, adjust the scan profile,
   windows or VM resources and repeat the pilot. More host limits create no
   CPU/RAM/SSD capacity. Reserve feed/backup/recovery hours explicitly.

## Data disk verification (no automatic disk changes)

Provision/mount the SSD and configure Docker's data-root through the host's
reviewed storage procedure **before deployment**. For an existing Docker store,
plan a separate stopped migration with a verified backup. This tool never formats
host disks, moves Docker data or changes daemon configuration.

Example intended layout: SSD mounted at `/srv/greenbone-data`, Docker root below
it at `/srv/greenbone-data/docker`, maintenance state/backups also on that SSD.
An independent backup share is mounted at `/mnt/greenbone-backup`. Adapt all
paths to the VM; system disk should contain OS/tooling, not unnoticed Docker
volumes. Configure mounts to fail visibly and verify mount persistence after a
reboot; a missing SSD mount must not silently expose a directory on the OS disk.

Read-only verification:

```bash
lsblk -f
findmnt -T /srv/greenbone-data
findmnt -T /srv/greenbone-data/docker
docker info --format '{{.DockerRootDir}}'
docker volume ls
docker volume inspect YOUR_ACTUAL_PSQL_VOLUME --format '{{.Mountpoint}}'
findmnt -T /actual/mountpoint/from/inspect
df -B1 /srv/greenbone-data /mnt/greenbone-backup
```

Set `DATA_MOUNT="/srv/greenbone-data"` only after verifying the real mount source.
`doctor` reports DockerRootDir and **every existing managed volume's actual
Mountpoint**, mount target/source and free bytes. It rejects Docker root/volumes
outside the expected mount; local volume driver options and alternative drivers
are unsupported. Without `DATA_MOUNT`, output explicitly says UNVERIFIED.
An unexpected nested mount also fails the exact target assertion; review it.
When host volume directories are protected, a pinned PostgreSQL-image helper
measures apparent sizes and entry overhead with read-only volume mounts and no
network access. The host mount table still identifies actual Mountpoints. An
accessible ancestor is used for disk statistics only when it is on the same
identified filesystem. Unsupported helper output or inaccessible filesystems
fail closed; a permission failure is never an estimate of zero bytes.

Illustrative output (numbers are not measurements):

```text
Storage DockerRootDir path=/srv/greenbone-data/docker mount=/srv/greenbone-data source=/dev/mapper/data free_bytes=...
Storage psql_data_vol path=/srv/greenbone-data/docker/volumes/.../_data mount=/srv/greenbone-data source=/dev/mapper/data free_bytes=...
Data disk verified against DATA_MOUNT
Full backup estimate volume_bytes=... feed_subset_bytes=... image_archive_bytes=... retained_backup_bytes=...
Space filesystem=... used_bytes=... free_bytes=... required_extra_with_reserve=... components=...
```

## Space accounting and retention

The fixed `MIN_FREE_MB` preflight remains. Additional backup/upgrade/restore
preflights enumerate current volume apparent sizes with file/block/tar overhead,
unique local image sizes (shared layers may be counted more than once), retained
backup/partial size and actual filesystem free space. All incremental requirements
on the **same filesystem device** are summed, then `SPACE_MARGIN_PERCENT` (default
20%) and the fixed minimum free reserve are added. Remote copy space and a real
write/fsync probe are checked **before services stop**. Existing usage already
includes live data, old backups, partials and retained restore generations: do
not subtract them twice from free space. No old backup is deleted to pass the gate.

For a spreadsheet, let D = existing live volumes/images, B = one complete full
backup, R = old full backups including the protected upgrade anchor, P = partials,
T = temporary/staging needs, V = uncompressed restored volumes, and I = image
load/staging allowance. Split this by filesystem, not by directory names:

- Backup peak total: `D + R + P + B_new + T`; add another `B_new` if the copy
  destination shares that filesystem. Both copies exist before retention.
- Restore peak total: current used space (including the source backup and **all
  old volume generations**) + `V_new + I + T`. The implementation allows three
  image-archive sizes for image loading/staging and rounds restored entries to
  filesystem blocks. Old generations stay allocated after success/failure.
- Required **free** space is the incremental part × 1.20 plus `MIN_FREE_MB`;
  temporary maintenance-state allowance is 16 MiB. This is a conservative estimate,
  not a reservation or hard upper bound. Concurrent external writers, storage
  quotas/thin provisioning, huge xattrs, daemon-specific extraction amplification,
  unexpected WAL/feed growth and inode exhaustion need VM monitoring. Free space
  is checked again before copying; filesystem/device accounting cannot infer
  shared backend pools behind different devices. Docker pulls remain subject to
  the fixed preflight and measured growth budget; mutable future feed size is
  unknowable. Leave measured pull/import/WAL headroom beyond the estimate.

Every snapshot retains feeds, static GSA data/logs, private state and **all
non-socket volumes**, plus the full application/feed image archive. This repeated
cost buys complete offline recovery. Snapshot logs show actual total bytes,
image-archive bytes and each volume archive's bytes; preflight separately reports
the feed-volume subset. Use these measurements to identify whether feed, image,
DB/report history or logs dominate. No deduplication or incremental restore chain
is introduced.

`BACKUP_COMPRESSION="gzip"` optionally streams each volume through the pinned
helper's `tar -z` directly to `.tar.gz`; it needs no second raw volume archive.
`images.tar` remains uncompressed and compatible with Docker load. Gzip increases
CPU/time and may barely reduce already compressed content; test its tar/gzip
support and CPU/I/O effect in the pilot. Capacity estimates deliberately use
uncompressed volume size. Checksums cover stored bytes; verifier and restore read
both legacy schema-1 `.tar` backups without a compression field and new `.tar.gz`
sets. The previous software version cannot read new compressed backups: retain
the matching recovery tooling with the independent copy. Mixed/unknown archive
inventories and corrupt compressed data fail verification.

`KEEP_BACKUPS=7` is only a count, **not** a claim that seven fit in 500 GB.
Illustration in GiB, not a measured forecast: a 500 GB marketed SSD is about
466 GiB; if live data/images use 120 GiB and a snapshot is 60 GiB, seven snapshots
alone need 420 GiB. Even four retained snapshots (240 GiB) plus a new one (60 GiB)
leave only 46 GiB before reserve, temporaries and an extra protected anchor;
restoring a further 100 GiB generation would not fit. Choose count from measured
p95 snapshot/restore sizes, RPO/RTO and separate-target capacity, or expand the
SSD. Keep an independently verified recovery copy. Retention runs only after a
verified snapshot/copy, restart and health; the newest upgrade anchor is protected
in addition to the normal count. Partials and old restore volumes require an
operator review. Separate-target retention is managed independently.

For Greenbone **report history**, propose initially three monthly cycles locally,
plus an agreed longer archive period on independent storage. Measure cost, legal
retention and compare/trend needs before adopting a period. Export full XML with
Info and agreed QoD/override filters, verify it and DefectDojo ingestion, and prove
restore before approving any deletion. Keep task definitions, credentials and
configuration. Review per-task Auto Delete Reports settings explicitly: the tool
never enables them and never deletes reports/results/findings by default.
DefectDojo's 500,000 accumulated records do not impose this same history policy.

## Independent target identity and loss during copy

For a mounted backup share, configure all three settings, using literal values
from `findmnt --json -T /mnt/greenbone-backup -o TARGET,SOURCE,FSTYPE,MAJ:MIN`:

```ini
BACKUP_COPY_DIR="/mnt/greenbone-backup/greenbone"
BACKUP_COPY_MOUNT="/mnt/greenbone-backup"
BACKUP_COPY_SOURCE="backup-server:/exports/greenbone"
```

For a local backup disk the source may be `/dev/mapper/backup`; keep the stable
mount identity consistent with the platform's mount configuration. The directory
must already exist. A missing mount, wrong source, unwritable target or insufficient
free bytes blocks backup/upgrade **before stop**. Without the optional assertions,
an existing directory alone cannot prove a remote backup; do not call this an
off-site copy or enable unattended operation until the identity contract is set.
Empty `BACKUP_COPY_DIR` means explicitly local-only.

Copy pins an open target directory descriptor and writes relative to it. If the
mount disappears, it cannot fall through to the underlying local directory.
Identity is rechecked before/after each file and publication, files and parent
are fsynced, and the checksum/inventory check precedes the rename from `.partial`.
I/O errors or target replacement report failure; local complete backup, partial
remote data and recovery marker remain. A hard network-filesystem kernel I/O
stall can outlive the Python budget; configure and test mount/server failure
behavior on the actual VM. No application can guarantee a remote target will
remain available after a successful final check. Server-side durability, access
control/encryption, retention and independent recoverability remain operator work.

## Duration budgets and recovery

| Setting | Default seconds | Scope |
| --- | --- | --- |
| STOP_TIMEOUT | 900 | Client wait/validation of graceful Docker stop |
| BACKUP_TIMEOUT | 21600 | Entire snapshot, checksums, verification and optional copy |
| RESTORE_TIMEOUT | 21600 | Entire restore, including verification, image loading, stop, imports and health |
| IMAGE_ARCHIVE_TIMEOUT | 7200 | Each image save/load, bounded by enclosing operation |
| COMMAND_TIMEOUT | 1800 | Other commands; individual calls also respect operation budget |
| HEALTH_TIMEOUT | 3600 | Functional readiness, bounded by enclosing restore budget |

All are independently validated positive integers. Archive/import subprocesses
use the appropriate long budget, rather than the generic 30-minute limit. Elapsed
seconds for stop, backup, restore and image save/load are logged on success/failure.
Hashing/copy loops check remaining budget between blocks. Failure-state
inspection/cleanup uses separate bounded 30-second calls even after the main
budget expires, so diagnosis can extend the failed operation duration. Set values from observed
p95 plus margin; defaults are not a promise that a large dataset finishes in six
hours. Cold backup downtime also includes stop/restart/feed import and remote copy.

Docker normally sends SIGKILL when a finite stop timeout expires. Maintenance
uses [Docker stop `--timeout -1`](https://docs.docker.com/reference/cli/docker/container/stop/),
with a bounded client wait. On expiry it inspects container states, raises an
error and keeps the recovery record, even if a late stop completes. It never
kills PostgreSQL to meet a clock deadline. For normal maintenance PostgreSQL must exit zero, other
services must be exited without exit 137/OOM, and no other running container may
use a managed volume. Restore uses the same stop procedure; it removes only
stopped containers and retains all old volumes. During disaster recovery only,
already-exited/crashed or never-started old containers may be replaced; containers
that are running when stop begins still need a verified clean shutdown. The old
crashed database is never used as the snapshot source. There is no timed `compose down`.

A killed Docker CLI does not necessarily cancel a daemon request. On archive
helper failure, the tool inspects and removes **only its named disposable helper**
and checks it is no longer running. Image save/load has no reliable Docker job
completion API; the tool checks daemon reachability and explicitly reports
completion unknown. Inspect `docker events`, `docker ps -a`, image IDs, daemon
logs and disk I/O before recovery. Do not run overlapping image operations merely
because the CLI returned. If Docker is unavailable, state is unknown and recovery
stays manual. Keep admission closed, inspect `status` and either complete the
known attempted set with `resume`, or restore a verified backup. Never delete the
marker to reenable timers; never restart old binaries against migrated data.

## Monthly scanning and daily maintenance without schedule bypass

Use one controlled external dispatcher/manual queue for monthly batches; leave
**all gvmd task schedules detached at all times** in this operating model. A future
assigned schedule still blocks the scan gate. Maintain batches outside the daily
maintenance interval and stop admitting new batches early enough for the slowest
batch to finish. If scans overrun, maintenance is skipped and alerted; scans are
not forcibly killed. Include spillover/retry days in the 4,000-host calculation.

The admission controller is an operator-owned technical control. At window entry:

1. Pause the dispatcher and disable its retry queue. Block GSA/API ingress at the
   reverse proxy/firewall for all scan-capable users, including admin tunnels.
   Disable other scheduled scripts/CI jobs that can call GMP or Docker.
2. Restrict Unix GMP socket/Docker access to the maintenance identity; pause local
   tools/containers mounting `gvmd_socket_vol`. An HTTP 503 alone does not block
   direct GMP clients. Keep only the maintenance Super Admin probe available.
3. Confirm no assigned schedules, active/queued/unknown tasks or alternative
   scanner-start paths remain. Wait for running work to complete; let the tool's
   unchanged scan gate perform its full checks. Keep these controls through
   restart and functional verification; do not reopen solely because the clock
   reached the end of the window. `AUTO_WINDOW_ACK=1` is an assertion, not a block.

The defaults are independent backup **02:00 UTC**, feed **03:00 UTC** timers.
They cannot guarantee a non-overlap: a long backup causes feed exit 75; a later
retry may instead exit 76 outside the window. Never break the lock, force the gate
or start another maintenance process. For production choose either explicitly
spaced measured schedules with a monitored in-window retry, or preferably one
operator-controlled daily sequence: disable the independent backup/feed timers
(and matching cron entries), then invoke **backup followed by feed-update only
if backup succeeds**, within a sufficiently long closed admission interval.
Both commands keep the shared lock and check their own entry window. A wrapper
is an operator deployment choice, not installed here. It must record each exit,
leave admission closed on failure/overrun, and reopen only after both operations
and `doctor` succeed. If a backup overruns the window, the feed command must
refuse; extend the next authorized window or investigate and explicitly retry.
Do not add `--maintenance-window` to unattended retries to bypass the time check.

Alert through existing monitoring on every nonzero unit/cron result, especially
75 (busy), 76 (window/scan gate), 70/incomplete state, copy failure and low disk.
`status` reports per-operation UTC completion timestamps from
`last-success.json`; only `finish` after functional health records a success. `resume` has its own
success stamp and does not mark an earlier failed backup/feed as completed.
Check actual GMP feed timestamps too: a successful feed command can find no new
upstream data. Suggested local alert thresholds: no successful daily backup/feed
for 26 hours (escalate at 48), stale loaded VT/SCAP/CERT feeds beyond the agreed
feed SLA, and a maintenance interval exceeding measured p95 plus margin. Cron
needs captured exit/output and an existing MTA/monitor; systemd needs failed-unit
and timestamp monitoring. A disabled timer without a replacement is an alert.
