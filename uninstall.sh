#!/usr/bin/env bash
# Remove maintenance tooling and schedules. Keep all recovery data and the stack.
set -Eeuo pipefail
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --help|-h) printf 'Usage: ./uninstall.sh [--dry-run]\nKeeps configuration, backups, images, volumes and containers.\n'; exit 0 ;;
        --purge) printf 'ERROR: --purge is retired: delete sensitive backups only after an independent verified copy.\n' >&2; exit 64 ;;
        *) printf 'ERROR: Unknown option\n' >&2; exit 64 ;;
    esac
done
if [[ "$(id -u)" == 0 ]]; then
    PREFIX="${PREFIX:-/usr/local}"
    SBIN_DIR="${SBIN_DIR:-$PREFIX/sbin}"
    CONFIG_DIR="${CONFIG_DIR:-/etc/greenbone}"
    DATA_DIR="${DATA_DIR:-/opt/greenbone/community-container}"
else
    PREFIX="${PREFIX:-$HOME/.local}"
    SBIN_DIR="${SBIN_DIR:-$PREFIX/bin}"
    CONFIG_DIR="${CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/greenbone}"
    DATA_DIR="${DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/greenbone/community-container}"
fi
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
CRON_DIR="${CRON_DIR:-/etc/cron.d}"
LOGROTATE_DIR="${LOGROTATE_DIR:-/etc/logrotate.d}"
RUN_USER="${RUN_USER:-$(id -un)}"
run() { if [[ "$DRY_RUN" == 1 ]]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi; }
for dir in "$SBIN_DIR" "$SYSTEMD_DIR" "$CRON_DIR" "$LOGROTATE_DIR"; do
    [[ "$dir" =~ ^/[a-zA-Z0-9_./:+-]+$ && "$dir" != / ]] || { printf 'ERROR: Unsafe path\n' >&2; exit 64; }
done
if [[ "$(id -u)" == 0 ]]; then
    for job in update feed backup version-check; do
        if [[ -f "$SYSTEMD_DIR/greenbone-$job.timer" ]]; then
            run systemctl disable --now "greenbone-$job.timer"
            if [[ "$DRY_RUN" == 0 ]] && systemctl is-active --quiet "greenbone-$job.service"; then
                printf 'ERROR: Maintenance service still active; wait for completion before uninstalling.\n' >&2
                exit 75
            fi
        fi
        run rm -f "$SYSTEMD_DIR/greenbone-$job.timer" "$SYSTEMD_DIR/greenbone-$job.service"
    done
    run systemctl daemon-reload
    run rm -f "$CRON_DIR/greenbone-update" "$CRON_DIR/greenbone-maintenance" "$LOGROTATE_DIR/greenbone-update"
fi
if command -v crontab >/dev/null; then
    cron_args=()
    [[ "$(id -u)" != 0 ]] || cron_args=(-u "$RUN_USER")
    old=''
    if old=$(crontab "${cron_args[@]}" -l 2>/dev/null); then :
    else rc=$?; [[ "$rc" == 1 ]] || exit "$rc"; fi
    filtered=$(printf '%s\n' "$old" | python3 -c 'import sys; print("".join(l for l in sys.stdin if "greenbone-update.sh" not in l and "# greenbone-update (managed by install.sh)" not in l), end="")')
    if [[ "$filtered" != "$old" ]]; then
        if [[ "$DRY_RUN" == 1 ]]; then printf '[dry-run] remove maintenance crontab entries\n'
        else printf '%s\n' "$filtered" | crontab "${cron_args[@]}" -; fi
    fi
fi
# shellcheck source=lib/tooling-lock.sh
source "$SRC_DIR/lib/tooling-lock.sh"
acquire_tooling_locks "$CONFIG_DIR/update.env" "${LOCK_FILE:-$DATA_DIR/maintenance.lock}"
run rm -f "$SBIN_DIR/greenbone-update.sh" "$SBIN_DIR/greenbone-maintenance/maintenance.py" "$SBIN_DIR/greenbone-maintenance/gmp_probe.py"
printf 'Tooling removed. Configuration, backups, image archives and Greenbone data retained.\n'
