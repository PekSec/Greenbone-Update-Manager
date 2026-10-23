#!/usr/bin/env bash
# Install maintenance tooling only. Never deploy a Greenbone stack.
set -Eeuo pipefail
umask 077
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IS_ROOT=0
[[ "$(id -u)" -ne 0 ]] || IS_ROOT=1
if [[ "$IS_ROOT" == 1 ]]; then
    PREFIX="${PREFIX:-/usr/local}"
    SBIN_DIR="${SBIN_DIR:-$PREFIX/sbin}"
    CONFIG_DIR="${CONFIG_DIR:-/etc/greenbone}"
    DATA_DIR="${DATA_DIR:-/opt/greenbone/community-container}"
    LOG_FILE="${LOG_FILE:-/var/log/greenbone-update.log}"
    RUN_USER="${RUN_USER:-root}"
else
    PREFIX="${PREFIX:-$HOME/.local}"
    SBIN_DIR="${SBIN_DIR:-$PREFIX/bin}"
    CONFIG_DIR="${CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/greenbone}"
    DATA_DIR="${DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/greenbone/community-container}"
    LOG_FILE="${LOG_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/greenbone/greenbone-update.log}"
    RUN_USER="${RUN_USER:-$(id -un)}"
fi
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
CRON_DIR="${CRON_DIR:-/etc/cron.d}"
LOGROTATE_DIR="${LOGROTATE_DIR:-/etc/logrotate.d}"
SCHEDULER="${SCHEDULER:-auto}"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 3 * * *}"
SYSTEMD_ONCALENDAR="${SYSTEMD_ONCALENDAR:-}"
CRON_MAILTO="${CRON_MAILTO:-$RUN_USER}"
NGINX_HOST="${NGINX_HOST:-localhost}"
NGINX_HTTP_PORT="${NGINX_HTTP_PORT:-9392}"
PROJECT="${PROJECT:-greenbone-community-edition}"
LOCK_FILE="${LOCK_FILE:-}"
DRY_RUN=0
USER_MODE=0
FORCE=0
usage() {
    cat <<'HELP'
Usage: ./install.sh [--user-mode] [options]
Installs tooling and schedules; does not install Docker or deploy Greenbone.
  --scheduler auto|systemd|cron|none
  --prefix DIR --sbin-dir DIR --config-dir DIR --data-dir DIR
  --log-file PATH --lock-file PATH --project NAME --user USER
  --schedule 'M H * * D'     Feed cron schedule (default: daily 03:00)
  --oncalendar VALUE        Explicit systemd feed calendar
  --mailto ADDRESS         Cron error/output recipient
  --host HOST --port PORT   Kept as metadata; edit reviewed Compose for networking
  --force                  Preserve old config as a backup and replace it
  --dry-run                No file, timer, crontab or Docker changes
  --help
Backup: daily 02:00; feed: daily 03:00; version report: Sunday 05:00.
Maintenance is disabled until AUTO_WINDOW_UTC and AUTO_WINDOW_ACK are configured.
HELP
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 64; }
run() { if [[ "$DRY_RUN" == 1 ]]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --force) FORCE=1; shift ;;
        --user-mode) USER_MODE=1; shift ;;
        --prefix) PREFIX="${2:?}"; SBIN_DIR="$PREFIX/sbin"; [[ "$IS_ROOT" == 1 ]] || SBIN_DIR="$PREFIX/bin"; shift 2 ;;
        --sbin-dir) SBIN_DIR="${2:?}"; shift 2 ;;
        --config-dir) CONFIG_DIR="${2:?}"; shift 2 ;;
        --data-dir) DATA_DIR="${2:?}"; shift 2 ;;
        --log-file) LOG_FILE="${2:?}"; shift 2 ;;
        --lock-file) LOCK_FILE="${2:?}"; shift 2 ;;
        --project) PROJECT="${2:?}"; shift 2 ;;
        --user) RUN_USER="${2:?}"; shift 2 ;;
        --scheduler) SCHEDULER="${2:?}"; shift 2 ;;
        --schedule) CRON_SCHEDULE="${2:?}"; shift 2 ;;
        --oncalendar) SYSTEMD_ONCALENDAR="${2:?}"; shift 2 ;;
        --mailto) CRON_MAILTO="${2:?}"; shift 2 ;;
        --host) NGINX_HOST="${2:?}"; shift 2 ;;
        --port) NGINX_HTTP_PORT="${2:?}"; shift 2 ;;
        --github-slug) shift 2 ;; # Compatibility with the former installer.
        *) die "Unknown option; run --help" ;;
    esac
done
[[ "$IS_ROOT" == 1 || "$USER_MODE" == 1 || "$DRY_RUN" == 1 ]] || die 'Run as root or pass --user-mode'
[[ "$IS_ROOT" == 1 || "$RUN_USER" == "$(id -un)" ]] || die 'User-mode installation cannot target another user'
CONFIG_FILE="$CONFIG_DIR/update.env"
BACKUP_DIR="$DATA_DIR/backups"
LIB_DIR="$SBIN_DIR/greenbone-maintenance"
LOCK_FILE="${LOCK_FILE:-$DATA_DIR/maintenance.lock}"
for value in "$SBIN_DIR" "$CONFIG_DIR" "$DATA_DIR" "$LOG_FILE" "$LOCK_FILE" "$SYSTEMD_DIR" "$CRON_DIR" "$LOGROTATE_DIR"; do
    [[ "$value" =~ ^/[a-zA-Z0-9_./:+-]+$ && "$value" != / ]] || die 'Paths must be absolute, without spaces or shell metacharacters'
done
[[ "$PROJECT" =~ ^[a-z0-9][a-z0-9_.-]{0,63}$ ]] || die 'Invalid project name'
[[ "$NGINX_HOST" =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'Invalid host metadata'
[[ "$NGINX_HTTP_PORT" =~ ^[0-9]{1,5}$ ]] || die 'Invalid port'
((10#$NGINX_HTTP_PORT > 0 && 10#$NGINX_HTTP_PORT <= 65535)) || die 'Invalid port range'
[[ "$CRON_MAILTO" =~ ^[a-zA-Z0-9_.@+-]+$ ]] || die 'Invalid mail recipient'
id "$RUN_USER" >/dev/null 2>&1 || die 'Run user does not exist'
RUN_GROUP="$(id -gn "$RUN_USER")"
command -v python3 >/dev/null || die 'Python 3 is required'
# Do not silently translate a cron expression to a different systemd schedule.
read -r minute hour dom month dow extra <<<"$CRON_SCHEDULE"
[[ -z "${extra:-}" && "$minute" =~ ^[0-5]?[0-9]$ && "$hour" =~ ^([01]?[0-9]|2[0-3])$ && "$dom" == '*' && "$month" == '*' && "$dow" =~ ^([0-7]|\*)$ ]] || die 'Use M H * * D (one weekday or *) for --schedule'
if [[ -z "$SYSTEMD_ONCALENDAR" ]]; then
    days=(Sun Mon Tue Wed Thu Fri Sat Sun)
    day=''
    [[ "$dow" == '*' ]] || day="${days[$dow]} "
    printf -v SYSTEMD_ONCALENDAR '%s*-*-* %02d:%02d:00' "$day" "$((10#$hour))" "$((10#$minute))"
fi
[[ "$SYSTEMD_ONCALENDAR" != *$'\n'* && "$SYSTEMD_ONCALENDAR" != *$'\r'* ]] || die 'Invalid calendar'
if [[ "$SCHEDULER" == auto ]]; then
    if [[ "$IS_ROOT" == 1 && -d /run/systemd/system ]] && command -v systemctl >/dev/null; then
        SCHEDULER=systemd
    elif command -v crontab >/dev/null || [[ "$IS_ROOT" == 1 && -d "$CRON_DIR" ]]; then
        SCHEDULER=cron
    else SCHEDULER=none; fi
fi
case "$SCHEDULER" in
    systemd) [[ "$IS_ROOT" == 1 ]] || die 'systemd scheduling requires root; use cron for user mode' ;;
    cron|none) ;;
    *) die 'Unknown scheduler' ;;
esac
export SBIN_DIR CONFIG_DIR CONFIG_FILE DATA_DIR BACKUP_DIR LIB_DIR LOG_FILE LOCK_FILE RUN_USER RUN_GROUP PROJECT NGINX_HOST NGINX_HTTP_PORT CRON_SCHEDULE CRON_MAILTO SYSTEMD_ONCALENDAR
render() {
    local source="$1" dest="$2" mode="$3"
    if [[ "$DRY_RUN" == 1 ]]; then printf '[dry-run] render %s -> %s\n' "$source" "$dest"; return; fi
    python3 - "$source" "$dest" "$mode" <<'PY'
import os, pathlib, re, sys, tempfile
source, dest, mode = sys.argv[1:]
text = pathlib.Path(source).read_text()
text = re.sub(r'@([A-Z_]+)@', lambda m: os.environ[m[1]], text)
assert not re.search(r'@[A-Z_]+@', text)
fd, temporary = tempfile.mkstemp(dir=pathlib.Path(dest).parent)
try:
    with os.fdopen(fd, 'w') as output:
        output.write(text)
    os.chmod(temporary, int(mode, 8))
    os.replace(temporary, dest)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY
}
filter_crontab() {
    python3 -c 'import sys; print("".join(line for line in sys.stdin if "greenbone-update.sh" not in line and "# greenbone-update (managed by install.sh)" not in line), end="")'
}
# Disable legacy software updates before replacing any executable. Never kill a
# running legacy upgrade: it may already be migrating the database.
if [[ "$IS_ROOT" == 1 ]]; then
    if [[ -f "$SYSTEMD_DIR/greenbone-update.timer" ]]; then
        run systemctl disable --now greenbone-update.timer
        if [[ "$DRY_RUN" == 0 ]] && systemctl is-active --quiet greenbone-update.service; then
            die 'Legacy update is active; wait for completion, inspect its result, then retry'
        fi
        run rm -f "$SYSTEMD_DIR/greenbone-update.timer" "$SYSTEMD_DIR/greenbone-update.service"
    fi
    run rm -f "$CRON_DIR/greenbone-update"
fi
if command -v crontab >/dev/null; then
    cron_args=()
    [[ "$IS_ROOT" == 0 ]] || cron_args=(-u "$RUN_USER")
    old_cron=''
    if old_cron=$(crontab "${cron_args[@]}" -l 2>/dev/null); then :
    else rc=$?; [[ "$rc" == 1 ]] || die 'Could not read crontab'; fi
    filtered=$(printf '%s\n' "$old_cron" | filter_crontab)
    if [[ "$filtered" != "$old_cron" ]]; then
        if [[ "$DRY_RUN" == 1 ]]; then printf '[dry-run] remove legacy maintenance crontab entries\n'
        else printf '%s\n' "$filtered" | crontab "${cron_args[@]}" -; fi
    fi
fi
# Disable previously managed timers when switching schedulers or reinstalling.
if [[ "$IS_ROOT" == 1 ]]; then
    for job in feed backup version-check; do
        if [[ -f "$SYSTEMD_DIR/greenbone-$job.timer" ]]; then run systemctl disable --now "greenbone-$job.timer"; fi
        if [[ "$DRY_RUN" == 0 ]] && [[ -f "$SYSTEMD_DIR/greenbone-$job.service" ]] && systemctl is-active --quiet "greenbone-$job.service"; then
            die 'Maintenance service is active; wait for completion and retry'
        fi
        run rm -f "$SYSTEMD_DIR/greenbone-$job.timer" "$SYSTEMD_DIR/greenbone-$job.service"
    done
    run rm -f "$CRON_DIR/greenbone-maintenance"
fi
# shellcheck source=lib/tooling-lock.sh
source "$SRC_DIR/lib/tooling-lock.sh"
acquire_tooling_locks "$CONFIG_FILE" "$LOCK_FILE"
if [[ "$DRY_RUN" == 1 ]]; then
    printf '[dry-run] tooling, private config, logrotate and %s scheduler; no deployment\n' "$SCHEDULER"
else
    install -d -m 0755 "$SBIN_DIR"
    for dir in "$CONFIG_DIR" "$DATA_DIR" "$BACKUP_DIR" "$LIB_DIR"; do
        [[ -d "$dir" ]] || install -d -m 0700 "$dir"
    done
    mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$LOCK_FILE")"
    if [[ "$IS_ROOT" == 1 ]]; then chown "$RUN_USER:$RUN_GROUP" "$CONFIG_DIR" "$DATA_DIR" "$BACKUP_DIR" "$LIB_DIR"; fi
fi
render "$SRC_DIR/bin/greenbone-update.sh" "$SBIN_DIR/greenbone-update.sh" 0755
for file in maintenance.py gmp_probe.py; do run install -m 0644 "$SRC_DIR/lib/$file" "$LIB_DIR/$file"; done
if [[ ! -f "$CONFIG_FILE" || "$FORCE" == 1 ]]; then
    [[ ! -f "$CONFIG_FILE" ]] || run cp -p "$CONFIG_FILE" "$CONFIG_FILE.bak.$(date +%Y%m%d-%H%M%S)"
    render "$SRC_DIR/config/update.env.example" "$CONFIG_FILE" 0600
    if [[ "$IS_ROOT" == 1 ]]; then run chown "$RUN_USER:$RUN_GROUP" "$CONFIG_FILE"; fi
else printf 'Existing configuration preserved; review new settings in config/update.env.example\n'; fi
if [[ "$IS_ROOT" == 1 && -d "$LOGROTATE_DIR" ]]; then
    render "$SRC_DIR/logrotate/greenbone-update.in" "$LOGROTATE_DIR/greenbone-update" 0644
fi
case "$SCHEDULER" in
    systemd)
        for job in feed backup version-check; do
            case "$job" in
                feed) JOB_COMMAND=feed-update; JOB_CALENDAR="$SYSTEMD_ONCALENDAR" ;;
                backup) JOB_COMMAND=backup; JOB_CALENDAR='*-*-* 02:00:00' ;;
                version-check) JOB_COMMAND=version-check; JOB_CALENDAR='Sun *-*-* 05:00:00' ;;
            esac
            export JOB_COMMAND JOB_CALENDAR
            render "$SRC_DIR/systemd/greenbone-update.service.in" "$SYSTEMD_DIR/greenbone-$job.service" 0644
            render "$SRC_DIR/systemd/greenbone-update.timer.in" "$SYSTEMD_DIR/greenbone-$job.timer" 0644
        done
        run systemctl daemon-reload
        for job in feed backup version-check; do run systemctl enable --now "greenbone-$job.timer"; done
        ;;
    cron)
        CRON_USER="$RUN_USER"; [[ "$IS_ROOT" == 1 ]] || CRON_USER=''
        export CRON_USER
        if [[ "$IS_ROOT" == 1 ]]; then
            render "$SRC_DIR/cron/greenbone-update.cron.in" "$CRON_DIR/greenbone-maintenance" 0644
        elif [[ "$DRY_RUN" == 1 ]]; then printf '[dry-run] install user crontab maintenance entries\n'
        else
            temp=$(mktemp)
            trap 'rm -f "$temp"' EXIT
            render "$SRC_DIR/cron/greenbone-update.cron.in" "$temp" 0600
            { printf '%s\n' "$filtered"; cat "$temp"; } | crontab -
        fi
        ;;
esac
printf 'Tooling ready. Review %s, then follow README for prepare/bootstrap. No stack deployed.\n' "$CONFIG_FILE"
