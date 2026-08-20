# Greenbone Update Manager

**Failsafe** update automation for Greenbone Community Edition (Docker).
It fetches the upstream `compose.yaml`, applies environment-specific patches,
validates the result, pulls the images, updates the stack and performs an
**automatic rollback** if the health check fails.

Sensitive data (host/IP, path, port) is not kept inside the script; it is read
from `update.env` and masked before anything is written to the log.

## Features

- Uninterrupted update: no `down`, just `pull` + `up -d` (only changed containers are recreated)
- Atomic config swap: download -> patch -> `docker compose config -q` -> commit
- Timestamped compose backup + automatic rollback
- Structural patching via `yq` when available, anchor-validated `sed` fallback otherwise
- Patch assertion (`STRICT_PATCH=1`): nothing is deployed if upstream changed
- Single instance via `flock`, `ERR`/`EXIT` traps, disk/dependency preflight
- Log masking + size-based rotation (+ logrotate)
- Safe prune: only dangling images, filtered by `until`

## Repo layout

```
.
├── bin/
│   └── greenbone-update.sh            # the script that does the work (template)
├── config/
│   └── update.env.example             # runtime config template
├── cron/
│   └── greenbone-update.cron.in       # /etc/cron.d entry template
├── logrotate/
│   └── greenbone-update.in            # /etc/logrotate.d entry template
├── systemd/
│   ├── greenbone-update.service.in    # oneshot update unit template
│   └── greenbone-update.timer.in      # weekly timer template
├── install.sh                         # install + scheduler (systemd or cron)
├── uninstall.sh                       # remove, --purge also drops state
├── README.md
├── LICENSE
├── .gitattributes
└── .gitignore
```

`update.env` itself is never committed; `install.sh` renders it from
`config/update.env.example` into the config directory.

## Installation

```bash
git clone https://github.com/<owner>/greenbone-update.git
cd greenbone-update
chmod +x install.sh uninstall.sh

# system-wide (root): /usr/local/sbin, /etc/greenbone, /opt/greenbone
sudo ./install.sh --host 10.0.0.1

# custom paths / different user / different distribution
sudo ./install.sh --host gvm.example.local --port 8080 \
     --data-dir /srv/greenbone --config-dir /etc/gvm \
     --user gvmuser --schedule "0 4 * * 0"

# without root (XDG directories + user crontab) - --user-mode is required
./install.sh --host 10.0.0.1 --user-mode

# see what it would do first
sudo ./install.sh --host 10.0.0.1 --dry-run
```

`install.sh` refuses to run without root unless `--user-mode` is passed, so a
system-wide install is never done by accident. `--host` is the only value it
cannot guess; on a re-install it is read back from the existing config file.

`install.sh` runs, in order: dependency check -> directories -> script
render+install -> config -> logrotate -> scheduler -> verification.

### Scheduler selection

| Environment            | Behavior                                                    |
| ---------------------- | ----------------------------------------------------------- |
| systemd (PID 1) + root | `greenbone-update.timer` (`Persistent=true`, 30 min jitter) |
| no systemd, root       | `/etc/cron.d/greenbone-update`                              |
| non-root               | user `crontab` entry                                        |
| `--scheduler none`     | no scheduler is installed                                   |

You pass a cron expression via `--schedule`; the systemd `OnCalendar` value is
derived from it automatically (or given explicitly with `--oncalendar`).

### install.sh options

`--host --port --project --prefix --sbin-dir --config-dir --data-dir --log-file
--lock-file --user --user-mode --scheduler --schedule --oncalendar --mailto
--github-slug --force --dry-run`

Run `./install.sh --help` for the grouped list with defaults filled in for your
environment. The same values can also be supplied as environment variables:
`PREFIX=/opt/tools sudo -E ./install.sh --host 10.0.0.1`

## Usage

```bash
sudo GB_DRY_RUN=1 /usr/local/sbin/greenbone-update.sh   # validate without deploying
sudo /usr/local/sbin/greenbone-update.sh                # run
tail -f /var/log/greenbone-update.log

systemctl list-timers greenbone-update.timer
journalctl -u greenbone-update.service -n 100
```

### Exit codes

| Code | Meaning                                                            |
| ---- | ------------------------------------------------------------------ |
| 0    | Success                                                            |
| 64   | Config missing/unreadable                                          |
| 69   | Preflight, download, patch or validation failure (stack untouched) |
| 70   | Deploy/health failed, rollback executed                            |
| 75   | Another instance is running (lock)                                 |

## Config (`update.env`)

Key settings: `DOWNLOAD_DIR`, `PROJECT`, `NGINX_HOST`, `NGINX_HTTP_PORT`,
`COMPOSE_URL`, `MAX_IPS_PER_TARGET`, `NOFILE_LIMIT`, `NPROC_LIMIT`,
`HEALTH_URL`, `HEALTH_TIMEOUT`, `MIN_FREE_MB`, `STRICT_PATCH`,
`PRUNE_ENABLED`, `PRUNE_UNTIL`, `KEEP_BACKUPS`, `MAX_LOG_BYTES`.

The file is installed with mode `0600` and kept out of the repo via `.gitignore`.

## Requirements

`docker` + `docker compose` v2, `curl`, `flock`, `sed`, `awk`, `coreutils`.
`yq` (v4) is optional but recommended for patch reliability.

## Uninstall

```bash
sudo ./uninstall.sh            # script + scheduler
sudo ./uninstall.sh --purge    # config, log and backups as well
```

The Greenbone stack (containers/volumes) is left untouched.

## Security note

The interface published with `--port` is exposed on all interfaces (`0.0.0.0`).
Restrict access with a firewall/reverse proxy and use TLS with a production
certificate.
