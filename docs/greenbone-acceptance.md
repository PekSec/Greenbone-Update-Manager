# Opt-in real Greenbone acceptance drill

**Not executed by unit tests, the PostgreSQL stand-in, installation or timers.**
This runbook is explicitly opt-in. Before executing it, obtain separate approval
for the lab deployment, named target IPs and scan window. The repository editing
task authorizes none of those actions. Do not use a production target or change
an existing task's settings. No real Greenbone upgrade is certified by this drill;
upgrade validation needs a separate source/target-version migration exercise.

Use an isolated lab VM with the intended CPU/RAM/storage, a new project and no
production data. Recovery can use that same lab VM's new volume generation, or
an isolated replacement VM with the **same PROJECT**. Restrict recovered scanners'
egress to the explicitly authorized lab targets and required feed/registry
endpoints; recovered credentials/tasks must never scan production accidentally.
Keep all task schedules detached, including after restore. Do not launch two
stacks on the same published ports or shared physical volumes.

## Record before execution

Create a private evidence directory outside the backup set; store no passwords
in shell history, command arguments, reports or public issue attachments. Record:

| Evidence | Required value |
| --- | --- |
| Approval | Operator, time, lab VM, allowed target IPs, scan window and profiles |
| Hardware | vCPU/RAM, SSD mount/source, DockerRootDir and actual volume paths |
| Software | OS/Docker/Compose, reviewed official source commit, component versions/digests, release bundle checksum |
| Scan | Task/target/config/port-list UUIDs, explicit max_hosts/max_checks, authentication method and filters |
| Timing | Feed load, scan start/end, graceful stop, backup, archive, copy, restore and health |
| Integrity | Backup inventory checksum; pre/post task/report/setting comparisons |
| Recovery | Old/new volume names, image IDs/content identity, result of rescan |

The `--maintenance-window` flag below is a human assertion that admission is
technically closed. It does not close GSA/GMP itself. Follow the admission controls
in [production preparation](production-readiness.md). Use the installed command
with the lab's private config; the example paths/IDs must be replaced deliberately.

## 1. Bootstrap the reviewed set and wait for feeds

Complete README's private configuration and reviewed `prepare` procedure first.
Use a source commit URL, verify each component's official release notes and the
actual scanner binary version. Set the lab's data-disk/backup-mount assertions.
Do not use the current upstream Compose automatically as a release upgrade.

```bash
sudo /usr/local/sbin/greenbone-update.sh bootstrap \
  --target lab-reviewed-set --maintenance-window
sudo /usr/local/sbin/greenbone-update.sh doctor
sudo /usr/local/sbin/greenbone-update.sh status
```

Configure the Feed Import Owner and maintenance Super Admin privately. If feed
load exceeds the configured health budget, keep admission closed, inspect the
failure, then explicitly `resume --maintenance-window` after fixing the cause.
Require a passing functional doctor, scanner verification and loaded VT/SCAP/CERT/
GVMD_DATA markers matching feed files. Save the actual GMP feed timestamps, not
just “containers running”. Confirm data-disk and remote-source assertions pass.

## 2. Authorized lab scan and baseline evidence

During the explicitly approved lab scan interval only, open the controlled
admission path for the operator. Create a new task with no assigned schedule,
start at 5 hosts / 2 checks (1/1 for fragile targets), and scan the allowlisted
Windows/Linux/network mix with approved credentials. Do not use the task wizard
on an unreviewed subnet. This runbook contains no automatic network scan command.

Require task Done, intended reachable/completed host counts, successful credential
checks where expected, correct config/port-list/target references and no target
harm. Export the full native XML report with Info and documented QoD/override
filters. Compare expected services/OS/package evidence on a known lab target;
zero results alone is not a successful test. Record report UUID, scan timestamps,
per-level counts, unfiltered result IDs/counts and report export SHA-256. Capture
scan performance metrics from the pilot table in production-readiness.md.

Export/review task definitions and relevant settings through authenticated GMP
using the pinned tools (credentials via private config/input). Retain at least
these logical values for before/after comparison:

- Task UUID/name, owner, target/config/scanner UUIDs and no assigned schedule.
- Task preferences including max_hosts/max_checks, alive test/port list and
  credential references; credential secrets are not exported to evidence.
- Report UUID, completion state, timestamps, hosts, result UUIDs/severities and
  selected known VT evidence, including Info.
- Feed Import Owner, representative user/settings/config/port-list/report-format
  UUIDs and read-only configuration files whose hashes should survive.

Full XML exports can include response-time/dynamic metadata or different sorting;
compare stable logical fields, not only a byte hash of a newly generated export.
The original export's hash is still useful to prove that stored evidence is intact.

## 3. Feed/application separation check

Close all admission again, confirm the task is idle and every schedule detached.
Save a private copy of `current.json` before and after the following command:

```bash
sudo /usr/local/sbin/greenbone-update.sh feed-update --maintenance-window
sudo /usr/local/sbin/greenbone-update.sh doctor
```

Compare `compose.services[service].image` for **every service outside the seven
FEEDS names** in `lib/maintenance.py`. They must be identical, including database,
migrator, GPG, scanner, initializer and tooling images. Feed service digests may
change or stay the same if upstream has no new content. Compare running container
image IDs to the accepted bundle too; retain feed timestamps and import durations.
Do not declare success from a no-op mutable tag pull alone. Any application digest
change fails this drill. No `prepare`/`upgrade` occurs during this step.

## 4. Complete offline backup

With admission still closed and scans idle:

```bash
sudo /usr/local/sbin/greenbone-update.sh backup --maintenance-window
sudo /usr/local/sbin/greenbone-update.sh verify-backup --backup /private/actual-backup
sudo /usr/local/sbin/greenbone-update.sh status
```

Record full/volume/image/feed byte costs and stop/archive/verify/copy/health times.
Verify the independent copy with `verify-backup` as well. Require PostgreSQL exit
zero and no running volume consumers during backup. Test both plain tar and gzip
on separate lab snapshots if enabling compression in production. Keep a known
valid independent copy throughout; never delete it for space to run this drill.

## 5. Isolated restore into new volumes

Close admission again after backup's restart. Stop other lab workloads that can
start scans. For the strongest offline proof, use a replacement lab VM with the
same architecture/PROJECT, recovery tooling, private config and backup available;
block registry/feed network access during restore. This demonstrates image/feed
self-containment. On the same VM, at least block registry/feed access, save the
old physical volume inventory, and require fresh `_restore_...` names. Same-host
restore alone does not prove a clean alternate Docker store works.

```bash
sudo /usr/local/sbin/greenbone-update.sh verify-backup --backup /private/actual-backup
sudo /usr/local/sbin/greenbone-update.sh restore --backup /private/actual-backup \
  --confirm-project YOUR_EXACT_LAB_PROJECT --maintenance-window
sudo /usr/local/sbin/greenbone-update.sh doctor
sudo /usr/local/sbin/greenbone-update.sh status
```

Require all persistent volumes to use **new** names, correct actual mounts,
healthy PostgreSQL/scanner/imports, no incomplete marker, archived image content
identities and immutable local image IDs. On the same VM, prove previous volume
names/data still exist and were not overwritten. Review recovered networking,
users and credentials privately before any admission change. The runtime config
is reviewed from backup, not automatically sourced onto the replacement host.

## 6. Integrity and authorized rescan

Compare all stable task/report/settings values from step 2, including the same
report UUID/results/Info counts and saved task preferences. Check credentials by
performing a second explicitly authorized scan of the **same lab targets** after
opening only the controlled operator path. Require Done and successful local
checks, inspect expected host/service/OS evidence, and explain feed-related result
differences rather than requiring identical vulnerabilities forever. Record a
new report UUID and keep the recovered old report. Close admission afterwards.

The drill passes only when both recovered history/settings and a new successful
scan are demonstrated. Record actual RPO (what scan data was lost after backup)
and RTO including verification, load, stop, extraction and health. No VM sizing or
4,000-host claim follows from scanning one small lab batch.

## Failure and large-data acceptance cases

Run these only in the isolated authorized lab, with independent good backup:

| Injection | Required result |
| --- | --- |
| Assigned future schedule, active scan, incomplete GMP inventory | Exit 76 before stop; no relaxed gate |
| Missing backup mount/wrong source/read-only or full target | Failure before stop, no local fallback copy |
| Disconnect share during copy | Failure/partial remote copy, local backup and marker retained; no success stamp |
| Stop wait shorter than observed PG shutdown | Error, no SIGKILL, no archive until clean stop, inspect daemon for continuing shutdown |
| Large reports/DB/feed set | Measure peak free bytes, inodes, CPU/I/O, all durations and successful full restore at the intended retention horizon |
| Image save/load wait too short | Error and marker, incomplete archive not accepted; inspect continuing Docker work before retry |
| Corrupt/truncate one tar/gzip or remove a required archive | Verify fails before restore stop/import |
| Interrupt volume import / exhaust target filesystem in lab | New partial volumes and old volumes preserved, marker retained, no automatic rollback |
| Backup still running at feed timer / window expired | Exit 75/76 observable, no forced overlap; retry only within authorized closed window |

After failed backup, inspect shutdown/image/helper state and partial copies before
resuming the accepted baseline. After failed restore, do not use `resume` to start
a partially imported generation: explicitly repeat restore from a verified backup
into another new generation, or follow a reviewed recovery diagnosis. Never prune
old generations until integrity, rescan and independent backup are accepted.

The separate `tests/docker_integration.py` uses PostgreSQL as a stand-in and can
exercise larger synthetic data, slow graceful shutdown and helper failures. Its
success does **not** sign off these real Greenbone cases. Keep a dated acceptance
record marking each case pass/fail/not run. Real source→target application upgrade
and schema migration, if not separately exercised, remain **unvalidated**.
