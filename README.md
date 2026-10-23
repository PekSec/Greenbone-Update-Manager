# Greenbone Update Manager

Controlled maintenance for a single Linux VM running Greenbone Community
Containers with Docker Compose. Application upgrades are manual. Scheduled work
is limited to **feed data, cold backups and version reports**. No Kubernetes,
package manager or host pip packages are needed.

This is a change from the old updater: calling `greenbone-update.sh` without a
command prints usage and exits. It never downloads and deploys the latest stack.
`install.sh` installs tooling; `bootstrap` creates a new Greenbone deployment.

## What is supported

Linux, Bash, Python **3.10+** (standard library), `flock`, Docker Engine and
Compose v2 supporting `config --format json` and `--pull never`. Version reports
also use Docker Buildx (`imagetools inspect`). Install Docker from its official
instructions before using this tool. Python-gvm runs inside the pinned official
`gvm-tools` container, not on the host.

The reviewed architecture is the official nginx/gsad split with
`pg-gvm-migrator`. Unknown services, volumes, bind mounts, external volumes,
custom secret/config mounts and unsupported feed releases are rejected. Extend
and test the backup/health adapter before adopting such a deployment. Remote
Docker daemons are not supported: the probe uses a local read-only bind mount.

Greenbone describes its [container guide](https://greenbone.github.io/docs/latest/22.4/container/index.html)
as a learning/test setup. This repository adds maintenance controls; it does not
certify production readiness. The monthly **2–3 thousand hosts** target must be
measured with representative scans. Official minimum hardware is not a sizing
recommendation for that target.

## First installation from zero

On your intended VM, first inspect without changing files or schedulers:

```bash
git clone https://github.com/PekSec/Greenbone-Update-Manager.git
cd Greenbone-Update-Manager
./install.sh --user-mode --scheduler none --dry-run
```

Install the tooling without activating schedules yet:

```bash
sudo ./install.sh --scheduler none
```

System-wide defaults are `/usr/local/sbin/greenbone-update.sh`,
`/etc/greenbone/update.env` and `/opt/greenbone/community-container`.
`--user-mode` uses XDG paths and supports user cron. `--help` lists path overrides;
installer paths must be absolute and contain no spaces or shell metacharacters.
Run maintenance as the configured `--user`, who needs Docker access. Do not run
root-owned configuration as a different user. Docker access is root-equivalent.

1. Review `/etc/greenbone/update.env`. Literal assignments are parsed, never
   sourced: no `$VAR`, `$(command)` or backticks. Existing config files are kept.
   Files containing credentials/config must be owned by the runtime user, mode
   `0600`. `--force` preserves the previous config before replacement.
2. Download the [official Compose source](https://greenbone.github.io/docs/latest/_static/compose.yaml)
   into a private review directory. Prefer a specific commit of
   [greenbone/docs](https://github.com/greenbone/docs), retain its URL and review
   release notes. A mutable upstream download is only an input for review.
3. Review the component set together. Leave loopback publishing in place for the
   pilot. `--host` and `--port` are retained as metadata for old callers; they do
   **not** rewrite networking. Change networking/ulimits in the reviewed Compose
   source explicitly. For remote access use an SSH tunnel or your controlled TLS
   ingress; do not expose the default login publicly.
4. Prepare an operator-named snapshot. `ce-2026-10-pilot01` below is a local release
   ID, not a Greenbone product version. Confirm the actual gvmd major from the
   official component release notes; the current reviewed policy supports 25/26.

```bash
sudo /usr/local/sbin/greenbone-update.sh prepare \
  --target ce-2026-10-pilot01 --compose /srv/greenbone-review/compose.yaml \
  --gvmd-major 26 --approve-set \
  --evidence https://greenbone.github.io/docs/latest/22.4/container/index.html
```

`prepare` pulls images but touches no running containers or volumes. It resolves
**every** image to `repository@sha256`, checks the gvmd binary, and saves the exact
Compose, original references, source, source hash, compatibility evidence and
bundle checksum in `releases/<ID>/`. Prefer an immutable official commit URL for
`--evidence`. This operator approval is necessary: there is no universal
machine-readable cross-component compatibility matrix. The script checks a
conservative boundary, not every possible internal interface.

Create `/etc/greenbone/gmp-credentials.json` using a private editor (not a shell
command containing the real password):

```json
{"username": "maintenance", "password": "REPLACE_IN_PRIVATE_EDITOR"}
```

The account must exist in gvmd. For initial bootstrap, use the initial admin
credentials privately until you can configure the dedicated account. Do not
commit this file or put passwords into process arguments. Set ownership to the
runtime user and mode `0600`.

```bash
sudo /usr/local/sbin/greenbone-update.sh bootstrap \
  --target ce-2026-10-pilot01 --maintenance-window
```

Initial feed import may take hours; `HEALTH_TIMEOUT` defaults to one hour. While
bootstrap waits, use another terminal to log into GSA over loopback/tunnel, change
the default administrator password, set the **Feed Import Owner**, and configure
credentials for the probe. A dedicated **Super Admin** is required for maintenance
scan checks so tasks belonging to other users are visible. Health-only checks
can use the initial admin during setup. Keep all task schedules detached.

If bootstrap times out, it leaves an incomplete record and the attempted set.
Finish credentials/feed-owner setup, inspect logs, then explicitly run
`resume --maintenance-window`. It retries the recorded set; it does not restore
or downgrade data. Do not delete the incomplete record to bypass the checks.

```bash
sudo /usr/local/sbin/greenbone-update.sh doctor
sudo /usr/local/sbin/greenbone-update.sh backup --maintenance-window
```

Before enabling unattended work, perform a representative scan and an isolated
restore drill. Measure hosts/hour, target reachability, authentication failures,
CPU/RAM/IO pressure, report growth, feed import time and backup duration. Derive
VM sizing and concurrency from the measured scan profile; increasing target
limits does not create scanner capacity.

## Daily commands and automation

| Command | Behavior |
| --- | --- |
| `status` | Report accepted release and incomplete operation; no deployment |
| `doctor` | Check daemon, disk, service lifecycle, GMP, scanner and loaded feed data |
| `feed-update` | Pull only seven approved feed services, keep all application identities |
| `version-check` | Compare upstream Compose hash and official rolling-channel digests; report only |
| `backup` | Stop stack, snapshot persistent volumes and images, verify/copy, restart and verify |
| `prepare --target ID ...` | Record reviewed Compose/image set; never deploy |
| `upgrade --target ID` | Check scans, take verified cold backup, deploy exact target, verify |
| `verify-backup --backup DIR` | Offline integrity/inventory/archive checks |
| `restore --backup DIR --confirm-project PROJECT` | Explicit data recovery to new volumes |
| `resume` | Explicitly continue the recorded failed operation, without data restoration |
| `adopt --target ID` | Record a verified existing stack; see migration below |

The deployment commands need `--maintenance-window` or a configured automatic
window. All commands accept `--dry-run`; `GB_DRY_RUN=1` is also supported. Dry-run
validates local configuration/available bundles only: **no Docker calls, lock,
log, schedule, backup or live-data writes**. It does not prove pulls or migrations
will succeed. From the repo, use
`GB_CONFIG_FILE=/private/path/update.env bash bin/greenbone-update.sh status`.

### Maintenance admission and its technical limit

`--maintenance-window` is an operator assertion, not a scan-cancellation switch.
Block all scan-creation paths (GSA/API clients, external schedulers and direct GMP
clients) for the whole operation. Detach every task's assigned schedule in GSA;
merely choosing a convenient hour does not stop gvmd's own scheduler. The tool
conservatively blocks **any assigned schedule**, even one apparently in the
future, and any active/queued/unrecognized task state. Stopped/interrupted tasks
are idle. GMP must return a complete unpaginated task inventory under Super Admin;
a query/auth/permission/schema error stops maintenance before data changes.

Checks run before work and again immediately before stopping the stack. There is
no atomic GMP “check idle and prohibit all new tasks” primitive here. An external
client could start a scan in the check/stop interval unless you enforce the
maintenance window. The shared file lock only coordinates these maintenance
commands. It does not govern GSA, gvmd schedules, other scripts or host admins.
Do not run Docker operations or another feed sync outside this tool concurrently.
The whole stack stops for feed copying and backups; expect downtime. Keep ingress
closed until the functional check passes, even if containers have already started.

For unattended backup/feed jobs set, for example:

```ini
AUTO_WINDOW_UTC="02:00-06:00"
AUTO_WINDOW_ACK="1"
```

Set `ACK=1` only after arranging that external admission block and detaching task
schedules. The time gate is checked at entry, not a wall-clock kill deadline; size
the window for the entire operation and keep it closed on failure/overrun. A long
backup can cause the later feed job to exit `75`; investigate and rerun during a
window. Automated jobs do not stop active scans to make room for maintenance.

After configuring and testing that contract:

```bash
sudo ./install.sh --scheduler systemd
systemctl list-timers 'greenbone-*'
journalctl -u greenbone-backup.service -u greenbone-feed.service \
  -u greenbone-version-check.service -n 100
```

The installer creates separate timers: daily backup at **02:00 UTC**, daily feed
at **03:00 UTC**, Sunday version report at **05:00 UTC**. No catch-up runs or jitter
are used, because missed jobs must not start outside a maintenance window. A
nonzero service exit is visible in systemd/journal; configure your existing
monitoring to alert on failed units. No email/Slack integration is silently added.

`--scheduler cron` remains supported for systems without systemd and user-mode
installs. Cron uses the **host timezone**; the admission gate always uses UTC.
`--schedule 'M H * * D'` changes the feed schedule; `--oncalendar` can override the
systemd calendar. Unsupported cron-to-calendar expressions fail rather than turn
into a different schedule. Cron retains job output for `MAILTO` (requires a local
MTA); it does not hide failures in `/dev/null`.

## Feed/application compatibility

The official [feed workflow](https://greenbone.github.io/docs/latest/22.4/container/workflows.html#performing-a-feed-synchronization)
identifies exactly these automatic data services:

`notus-data`, `vulnerability-tests`, `scap-data`, `dfn-cert-data`,
`cert-bund-data`, `report-formats`, `data-objects`.

Everything else is held as application/trust/bootstrap state: `gvmd`, `gsad`,
`gsa`, `nginx`, `ospd-openvas`, `openvas`, `openvasd`, `redis-server`, `pg-gvm`,
`pg-gvm-migrator`, `gvm-tools`, `configure-openvas`, `gvm-config`, **`gpg-data`**.
A data-copying image such as `gsa` is still application code, not a feed.
Feed updates follow the seven official `latest` data-image channels with the
recorded `FEED_RELEASE`, even when adoption started from digest-pinned feed
images. Version reports compare the official channels of this reviewed
architecture (`stable`, `stable-slim` or `latest`); application channels are
never used for deployment without preparing and selecting a new target.

The adapter supports the current reviewed Compose `FEED_RELEASE=24.10` with gvmd
25/26. All three release-selecting data services must agree. Official
[feed-sync guidance](https://github.com/greenbone/greenbone-feed-sync#version)
warns about older gvmd compatibility and gvmd 24 feed-loading problems. This
local policy is intentionally narrower; a new feed series/manager major fails
closed and needs a reviewed code/policy update, not an automatic application
upgrade. GPG rotations similarly require a controlled approved set update.

Health checks include successful exited initializers, healthy keep-alive copying
containers, all application daemons, authenticated GMP, OpenVAS scanner
verification, four feed types and imported configs/port lists/report formats.
They compare the scanner VT version and database NVT/SCAP/CERT import markers with
on-disk feed versions, and feed-object database modification times with XML file
mtimes. These are read-only checks using the upstream gvmd schema; unknown
schemas/layouts fail closed. An HTTP reply alone is never considered healthy.
A representative successful authenticated scan is still a separate pilot check.

## Controlled upgrade

Review the official [rolling-release changes](https://greenbone.github.io/docs/latest/changelog.html)
and component release notes. Prepare a **new** local target ID as above, review
its saved bundle and planned migration. `version-check` does not prepare or
deploy a target and cannot prove that a remote image is semantically “newer”.

```bash
sudo /usr/local/sbin/greenbone-update.sh version-check
sudo /usr/local/sbin/greenbone-update.sh upgrade \
  --target ce-reviewed-next --maintenance-window --dry-run
sudo /usr/local/sbin/greenbone-update.sh upgrade \
  --target ce-reviewed-next --maintenance-window
```

Order: prerequisites/compatibility → scan gate → target digest pulls → second
scan gate → stop and verify no running volume consumers → consistent backup and
optional separate copy → target deploy/migration → functional health → accept
new current bundle. Existing physical volume names are preserved, including after
restore. Pull failures occur before downtime. A pre-upgrade backup is protected
from normal retention by `last-upgrade-backup`.

A partial deploy/migration or health failure leaves `incomplete-operation.json`
and `attempt.json`; automated writes stay blocked. It does **not** silently start
old images or restore data. Migration can make the database incompatible with old
binaries. Choose an explicit `resume` of the exact recorded target, or restore
the pre-upgrade database **and** persistent state **and** saved images together.

## Backups and actual recovery

`backup` first verifies idle/healthy state, then stops every stack container. It
rejects a forced-killed container and any other running container mounting a
stack volume. No active PostgreSQL volume is copied. This cold approach provides
a consistent filesystem set across PostgreSQL, gvmd state and keys at the cost
of downtime and disk space. Custom PostgreSQL tablespaces/external mounts are not
supported by this adapter.

The private backup contains:

- Full stopped PostgreSQL cluster/WAL and gvmd persistent data (reports, tasks,
  scan credentials, settings and keys).
- OpenVAS configuration, GPG data and nginx configuration/certificates/keys.
- Exact accepted Compose/image manifest, runtime config and GMP credentials.
- Re-downloadable feed, GSA static data and logs as well, so restoration needs
  no current feed/registry download. These are replaceable data; the database,
  custom configuration and private keys are irreplaceable. Socket volumes are
  omitted and recreated empty; they are not durable data.
- Docker image archive with saved local tags and content fingerprints. Restore
  validates these and uses immutable local IDs; it does not assume `docker load`
  restores registry digest associations.

Files are `0600`, backup directories `0700`; treat the whole backup as a secret.
SHA-256 inventory, volume tar paths, image archive inventory and release checksum
are checked before completion and before restore. These hashes detect damage,
not malicious replacement by someone who can rewrite both data and checksums.
Use trusted access-controlled backups and encrypt the separate disk/share at
rest through your existing platform. No encryption key is stored by this tool.

Set `BACKUP_COPY_DIR` to an **existing separate mounted disk/share directory**.
The copy is verified and renamed from `.partial` only when complete. The tool does
not mount storage, store SSH credentials or claim a second directory is off-site.
On copy failure it reports failure, keeps the local completed/partial backup and
recovery marker; use `resume` to reopen the known set after diagnosis. An empty
copy setting explicitly means the backup is local-only.

`KEEP_BACKUPS=7` keeps the latest seven complete local snapshots, plus the newest
upgrade recovery anchor. Old snapshots are verified before deletion; partials
and unknown files are not pruned. Separate-target retention is your responsibility.
Image tags are retained and **no image or volume prune runs**. Image archives also
survive host image pruning; external `docker system prune -a` can still remove
local tagged images, so keep the independent backup. Budget storage for image
archives, data, duplicate restore volumes and a verified external copy.

To recover, first close ingress/scan admission and stop external scheduling. If
the manager is available, scans must be idle. An incomplete marker never bypasses
that gate while any project container is running. If manager failure prevents a
query, manually investigate and stop the failed project in the maintenance window
before restore; there is no “ignore active scans” option.

```bash
sudo /usr/local/sbin/greenbone-update.sh status
sudo /usr/local/sbin/greenbone-update.sh verify-backup --backup /secure/backup-YYYY...
sudo /usr/local/sbin/greenbone-update.sh restore --backup /secure/backup-YYYY... \
  --confirm-project greenbone-community-edition --maintenance-window
sudo /usr/local/sbin/greenbone-update.sh doctor
```

Restore verifies the complete backup **before stopping/replacing anything**, loads
saved images, creates fresh named volumes, restores ownership/permissions and
saved credentials, then starts and verifies the saved set. It never overwrites
old volumes or removes them automatically. The operator confirmation acknowledges
that this replaces the **active deployment/data selection**. A failed restore
keeps the marker, old volumes and any partially restored new volumes; it never
reports a successful rollback. Inspect and explicitly retry restore or resume
only after understanding the failure. `resume` before data mutation restarts the
accepted baseline; once deployment starts it can only continue the recorded
attempted set, never the old images against migrated data.

For whole-VM loss, install the tooling, set the same `PROJECT` and appropriate
new paths in a private config, attach the separate backup and run `verify-backup`
then `restore`. The saved config is included for review, but not blindly sourced
or overwritten onto a replacement host. The exact Compose/volume state and saved
probe identity are restored. Check VM networking, TLS and Docker architecture
match the original before reopening admission.

## Migration from the old updater

1. Inventory systemd and all relevant user/root cron jobs, including custom names,
   wrappers and external schedulers. The installer disables/removes the known
   `greenbone-update.timer/service`, `/etc/cron.d/greenbone-update` and direct
   `greenbone-update.sh` crontab calls for the selected runtime user. It cannot
   discover renamed copies or another host's automation; disable those manually.
2. Wait for a running old update to finish and inspect its result. Installation
   refuses an active service/maintenance lock; it does not kill a migration.
   The old config `LOCK_FILE` is honored during tooling replacement. New timer
   and cron maintenance uses the same runtime lock, including backup/restore.
3. Install with `--scheduler none`. Preserve existing config, remove old unsafe
   options or replace it after reviewing `config/update.env.example`. Old prune
   and automatic software-update options are ignored by the new engine.
4. This user currently has no running stack: use the zero-install procedure.
   For an existing supported stack, prepare the **exact running image set** from
   its local reviewed Compose (use running repository digests, not moved tags),
   preserve project/volume names, configure a Super Admin and run
   `adopt --target ID --maintenance-window`. Adoption verifies health, actual
   mounts and actual image IDs; a mismatch refuses registration. Older unsupported
   topologies need a separately reviewed migration/backup before adoption.
5. Take and test a cold backup. Configure admission windows, then install the
   chosen scheduler. Confirm no legacy automatic application updates remain.

## Failures and exit codes

| Code | Meaning |
| --- | --- |
| 0 | Requested operation succeeded |
| 2 | Invalid/missing CLI arguments (including no command) |
| 64 | Invalid configuration, permissions, unsupported input or unreadable state |
| 69 | Prerequisite, pull, compatibility or command failure; inspect stage/marker |
| 70 | Incomplete operation or interruption; manual recovery required |
| 73 | Functional health failed/timed out |
| 74 | Backup/archive/restore data failure |
| 75 | Maintenance lock is held |
| 76 | Scan state unknown/active/scheduled, or no valid maintenance window |

Check both the exit code **and `status`**. A `69` during deploy can leave an
incomplete migration; it does not mean “unchanged”. Stage messages and service
exit codes are written to the journal; successful stages also go to the private
log. Raw Docker/GMP output and command arguments are deliberately suppressed to
avoid secret leaks. Inspect Compose logs locally with appropriate permissions;
do not paste raw database/config logs into public tickets.

For failed init/data services inspect the specific container exit status/logs.
An initializer exited `0` is normal. For slow feed startup allow time, check disk,
Feed Import Owner, scanner connection and feed SQL markers. Unknown layouts or
schemas require review; they are not worked around by pulling latest applications.
Never delete `incomplete-operation.json` to enable a timer after a failure.

`sudo ./uninstall.sh` removes tooling/schedules, keeping configuration, images,
backups and all stack data. `--purge` now refuses: recovery data must be deleted
manually only after an independent verified copy. Uninstall does not stop or
remove the Greenbone deployment.

## Development verification

```bash
python3 -m unittest discover -s tests -v
for script in install.sh uninstall.sh bin/greenbone-update.sh lib/tooling-lock.sh; do bash -n "$script" || exit; done
shellcheck -x install.sh uninstall.sh bin/greenbone-update.sh lib/tooling-lock.sh
# Opt-in; requires Docker and a cached postgres:17-alpine image:
python3 tests/docker_integration.py
```

The isolated Docker test uses random `gb-test-*` names, real stopped PostgreSQL,
image save/load, all declared volume archives, checksums and restoration into new
volumes. It checks old database content and application settings survive. It
starts no Greenbone service; GMP/Greenbone compatibility are explicit stand-ins.
It deletes only its own test resources. This test is not a real Greenbone
migration, scanner/feed import test, clean alternate-Docker-store certification
or 2–3k-host capacity measurement. Run a full Greenbone pilot and disaster-recovery
drill on the VM before enabling unattended jobs. See
[maintenance decisions and sources](docs/maintenance-design.md).
