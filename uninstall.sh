#!/usr/bin/env bash
#==============================================================================
# greenbone-update - uninstaller
#   sudo ./uninstall.sh              # removes the script + the scheduler
#   sudo ./uninstall.sh --purge      # also deletes config, log and backups
# NOTE: DOES NOT TOUCH the Greenbone stack (docker volumes/containers).
#==============================================================================
set -Eeuo pipefail

IS_ROOT=0; [ "$(id -u)" -eq 0 ] && IS_ROOT=1
if [ "$IS_ROOT" -eq 1 ]; then
    SBIN_DIR="${SBIN_DIR:-/usr/local/sbin}"
    CONFIG_DIR="${CONFIG_DIR:-/etc/greenbone}"
    DATA_DIR="${DATA_DIR:-/opt/greenbone/community-container}"
    LOG_FILE="${LOG_FILE:-/var/log/greenbone-update.log}"
else
    SBIN_DIR="${SBIN_DIR:-$HOME/.local/bin}"
    CONFIG_DIR="${CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/greenbone}"
    DATA_DIR="${DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/greenbone/community-container}"
    LOG_FILE="${LOG_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/greenbone/greenbone-update.log}"
fi
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

info() { printf '[*] %s\n' "$*"; }

if command -v systemctl >/dev/null 2>&1 && [ -f /etc/systemd/system/greenbone-update.timer ]; then
    info "removing systemd timer"
    systemctl disable --now greenbone-update.timer >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/greenbone-update.timer /etc/systemd/system/greenbone-update.service
    systemctl daemon-reload || true
fi

if [ -f /etc/cron.d/greenbone-update ]; then
    info "removing /etc/cron.d/greenbone-update"
    rm -f /etc/cron.d/greenbone-update
fi

if command -v crontab >/dev/null 2>&1 && crontab -l 2>/dev/null | grep -q "greenbone-update.sh"; then
    info "removing the user crontab entry"
    crontab -l 2>/dev/null \
    | grep -v "# greenbone-update (managed by install.sh)" \
    | grep -v "greenbone-update.sh" | crontab -
fi

rm -f "$SBIN_DIR/greenbone-update.sh" /etc/logrotate.d/greenbone-update
info "script removed"

if [ "$PURGE" -eq 1 ]; then
    info "PURGE: deleting config, log and backups"
    rm -rf "$CONFIG_DIR" "$DATA_DIR/backups"
    rm -f "$LOG_FILE" "${LOG_FILE}.1"
else
    info "config/log/backups kept (use --purge to delete them)"
fi
info "done"
