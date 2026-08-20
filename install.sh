#!/usr/bin/env bash
#==============================================================================
# greenbone-update - installer
#
# Distribution/user/directory agnostic. All paths are variables; when not run
# as root it installs into the XDG directories. Scheduler: systemd timer if
# available, otherwise cron.
#
#   sudo ./install.sh --host 10.0.0.1
#   sudo ./install.sh --host gvm.local --port 8080 --schedule "0 4 * * 0"
#   sudo ./install.sh --host 10.0.0.1 --scheduler cron --dry-run
#   ./install.sh --host 10.0.0.1 --user-mode  # non-root -> under ~/.local
#==============================================================================
set -Eeuo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GITHUB_SLUG="${GITHUB_SLUG:-<owner>/greenbone-update}"

#------------------------------------------------------------------- defaults
IS_ROOT=0; [ "$(id -u)" -eq 0 ] && IS_ROOT=1

if [ "$IS_ROOT" -eq 1 ]; then
  PREFIX="${PREFIX:-/usr/local}"
  SBIN_DIR="${SBIN_DIR:-$PREFIX/sbin}"
  CONFIG_DIR="${CONFIG_DIR:-/etc/greenbone}"
  DATA_DIR="${DATA_DIR:-/opt/greenbone/community-container}"
  LOG_DIR="${LOG_DIR:-/var/log}"
  LOCK_DIR="${LOCK_DIR:-/var/lock}"
  RUN_USER="${RUN_USER:-root}"
else
  PREFIX="${PREFIX:-$HOME/.local}"
  SBIN_DIR="${SBIN_DIR:-$PREFIX/bin}"
  CONFIG_DIR="${CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/greenbone}"
  DATA_DIR="${DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/greenbone/community-container}"
  LOG_DIR="${LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/greenbone}"
  LOCK_DIR="${LOCK_DIR:-${XDG_RUNTIME_DIR:-/tmp}}"
  RUN_USER="${RUN_USER:-$(id -un)}"
fi

CONFIG_FILE="${CONFIG_FILE:-$CONFIG_DIR/update.env}"
BACKUP_DIR="${BACKUP_DIR:-$DATA_DIR/backups}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/greenbone-update.log}"
LOCK_FILE="${LOCK_FILE:-$LOCK_DIR/greenbone-update.lock}"

PROJECT="${PROJECT:-greenbone-community-edition}"
NGINX_HOST="${NGINX_HOST:-}"
NGINX_HTTP_PORT="${NGINX_HTTP_PORT:-80}"

SCHEDULER="${SCHEDULER:-auto}"          # auto|systemd|cron|none
CRON_SCHEDULE="${CRON_SCHEDULE:-0 3 * * 0}"
SYSTEMD_ONCALENDAR="${SYSTEMD_ONCALENDAR:-}"
CRON_MAILTO="${CRON_MAILTO:-root}"
DRY_RUN=0; FORCE=0; USER_MODE=0

#-------------------------------------------------------------------- helpers
c_r=""; c_g=""; c_y=""; c_0=""
if [ -t 1 ]; then c_r=$'\033[31m'; c_g=$'\033[32m'; c_y=$'\033[33m'; c_0=$'\033[0m'; fi
info() { printf '%s[*]%s %s\n' "$c_g" "$c_0" "$*"; }
warn() { printf '%s[!]%s %s\n' "$c_y" "$c_0" "$*" >&2; }
die()  { printf '%s[x]%s %s\n' "$c_r" "$c_0" "$*" >&2; exit 1; }
run()  { if [ "$DRY_RUN" -eq 1 ]; then printf '    [dry] %s\n' "$*"; else "$@"; fi; }

usage() {
  cat <<EOF
Usage: sudo $0 --host <IP|FQDN> [options]

Required (on first install):
  --host <val>            Host/IP the Greenbone web interface is published on.
                          Re-installs reuse the value already in the config file.

Paths and identity:
  --prefix <dir>          Install prefix, sets --sbin-dir (default: $PREFIX)
  --sbin-dir <dir>        Where greenbone-update.sh is installed (default: $SBIN_DIR)
  --config-dir <dir>      Holds update.env, mode 0600 (default: $CONFIG_DIR)
  --data-dir <dir>        compose.yaml + timestamped backups (default: $DATA_DIR)
  --log-file <path>       Log file (default: $LOG_FILE)
  --lock-file <path>      flock file, one run at a time (default: $LOCK_FILE)
  --user <name>           User the scheduled job runs as (default: $RUN_USER)
  --user-mode             Install into your home directory instead of system-wide.
                          Required to run this installer without root.

Stack:
  --port <n>              HTTP port the web interface is published on (default: $NGINX_HTTP_PORT)
  --project <name>        docker compose project name (default: $PROJECT)

Scheduling:
  --scheduler <mode>      auto|systemd|cron|none (default: $SCHEDULER)
                          auto = systemd timer if available, else cron
  --schedule <cron>       5-field cron expression (default: "$CRON_SCHEDULE")
  --oncalendar <val>      systemd OnCalendar, derived from --schedule if omitted
  --mailto <addr>         cron MAILTO (default: $CRON_MAILTO)

Other:
  --github-slug <o/repo>  owner/repo used in the systemd Documentation= URL
                          (default: $GITHUB_SLUG)
  --force                 Overwrite an existing config file instead of keeping it
  --dry-run               Write nothing, list what would be done
  -h|--help               This help

Every option can also be given as an environment variable:
  PREFIX=/opt/tools sudo -E $0 --host 10.0.0.1
EOF
}

require_root() {
  cat >&2 <<EOF
${c_r}[x]${c_0} This installer must run as root.

    Re-run:  sudo $0 --host <IP|FQDN>

    To install into your home directory instead,
    pass --user-mode explicitly:

             $0 --host <IP|FQDN> --user-mode

    Run '$0 --help' for the full list of options.
EOF
  exit 1
}

#------------------------------------------------------------------ arg parsing
while [ $# -gt 0 ]; do
  case "$1" in
    --host)        NGINX_HOST="${2:?}"; shift 2 ;;
    --port)        NGINX_HTTP_PORT="${2:?}"; shift 2 ;;
    --project)     PROJECT="${2:?}"; shift 2 ;;
    --prefix)      PREFIX="${2:?}"; SBIN_DIR="$PREFIX/sbin"; [ "$IS_ROOT" -eq 0 ] && SBIN_DIR="$PREFIX/bin"; shift 2 ;;
    --sbin-dir)    SBIN_DIR="${2:?}"; shift 2 ;;
    --config-dir)  CONFIG_DIR="${2:?}"; CONFIG_FILE="$CONFIG_DIR/update.env"; shift 2 ;;
    --data-dir)    DATA_DIR="${2:?}"; BACKUP_DIR="$DATA_DIR/backups"; shift 2 ;;
    --log-file)    LOG_FILE="${2:?}"; LOG_DIR="$(dirname "$LOG_FILE")"; shift 2 ;;
    --lock-file)   LOCK_FILE="${2:?}"; LOCK_DIR="$(dirname "$LOCK_FILE")"; shift 2 ;;
    --user)        RUN_USER="${2:?}"; shift 2 ;;
    --scheduler)   SCHEDULER="${2:?}"; shift 2 ;;
    --schedule)    CRON_SCHEDULE="${2:?}"; shift 2 ;;
    --oncalendar)  SYSTEMD_ONCALENDAR="${2:?}"; shift 2 ;;
    --mailto)      CRON_MAILTO="${2:?}"; shift 2 ;;
    --github-slug) GITHUB_SLUG="${2:?}"; shift 2 ;;
    --user-mode)   USER_MODE=1; shift ;;
    --force)       FORCE=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             usage; die "unknown parameter: $1" ;;
  esac
done

# Root is required unless the user explicitly asked for a home-directory install.
[ "$IS_ROOT" -eq 1 ] || [ "$USER_MODE" -eq 1 ] || require_root

# refresh derived paths after --prefix
BACKUP_DIR="$DATA_DIR/backups"
CONFIG_FILE="$CONFIG_DIR/update.env"

#--------------------------------------------------------------------- checks
[ -f "$SRC_DIR/bin/greenbone-update.sh" ] || die "incomplete repo layout: bin/greenbone-update.sh"

if [ -z "$NGINX_HOST" ]; then
  if [ -r "$CONFIG_FILE" ]; then
    NGINX_HOST=$(. "$CONFIG_FILE" 2>/dev/null; printf '%s' "${NGINX_HOST:-}")
    [ -n "$NGINX_HOST" ] && info "host taken from existing config: $NGINX_HOST"
  fi
fi
if [ -z "$NGINX_HOST" ]; then
  usage
  cat >&2 <<EOF

${c_r}[x]${c_0} --host is required.

    It is the host or IP the Greenbone web interface will be published on,
    and it is the one value this installer cannot guess. Example:

        sudo $0 --host 10.0.0.1
        sudo $0 --host gvm.example.local --port 8080

    No existing config was found at $CONFIG_FILE,
    so there is nothing to reuse the previous value from.
EOF
  exit 1
fi

case "$NGINX_HTTP_PORT" in ''|*[!0-9]*) die "invalid port: $NGINX_HTTP_PORT" ;; esac
[ "$(printf '%s\n' "$CRON_SCHEDULE" | awk '{print NF}')" -eq 5 ] \
  || die "cron expression must have 5 fields: '$CRON_SCHEDULE'"

id -u "$RUN_USER" >/dev/null 2>&1 || die "no such user: $RUN_USER"
RUN_GROUP="$(id -gn "$RUN_USER")"

check_deps() {
  local miss=""
  for c in docker curl sed awk flock stat; do
    command -v "$c" >/dev/null 2>&1 || miss="$miss $c"
  done
  [ -n "$miss" ] && warn "missing command(s):$miss - install them before the script runs"
  if command -v docker >/dev/null 2>&1; then
    docker compose version >/dev/null 2>&1 || warn "docker compose v2 plugin not found (v1 is not supported)"
    if [ "$RUN_USER" != "root" ] && ! id -nG "$RUN_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
      warn "$RUN_USER is not in the 'docker' group; the script may not be able to reach docker"
    fi
  fi
  command -v yq >/dev/null 2>&1 || warn "yq not found - the sed fallback patch will be used (yq recommended)"
}

#-------------------------------------------------------------- cron -> OnCalendar
cron_to_oncalendar() {
  local m h dom mon dow days
  read -r m h dom mon dow <<<"$1"
  case "$m$h" in *[!0-9]*) printf 'weekly'; return ;; esac
  local hhmm; hhmm=$(printf '%02d:%02d:00' "$h" "$m")
  if [ "$dom" = "*" ] && [ "$mon" = "*" ]; then
    if [ "$dow" = "*" ]; then printf '*-*-*%s' " $hhmm"; return; fi
    case "$dow" in
      0|7) days=Sun ;; 1) days=Mon ;; 2) days=Tue ;; 3) days=Wed ;;
      4) days=Thu ;; 5) days=Fri ;; 6) days=Sat ;;
      *) printf 'weekly'; return ;;
    esac
    printf '%s *-*-* %s' "$days" "$hhmm"; return
  fi
  printf '*-*-* %s' "$hhmm"
}
[ -n "$SYSTEMD_ONCALENDAR" ] || SYSTEMD_ONCALENDAR="$(cron_to_oncalendar "$CRON_SCHEDULE")"

#---------------------------------------------------------- scheduler detection
detect_scheduler() {
  if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1 && [ "$IS_ROOT" -eq 1 ]; then
    printf 'systemd'
  elif command -v crontab >/dev/null 2>&1 || [ -d /etc/cron.d ]; then
    printf 'cron'
  else
    printf 'none'
  fi
}
[ "$SCHEDULER" = "auto" ] && SCHEDULER="$(detect_scheduler)"
case "$SCHEDULER" in systemd|cron|none) ;; *) die "invalid --scheduler: $SCHEDULER" ;; esac
if [ "$SCHEDULER" = "systemd" ] && { [ ! -d /run/systemd/system ] || [ "$IS_ROOT" -eq 0 ]; }; then
  warn "systemd is not usable, falling back to cron"; SCHEDULER="cron"
fi

#---------------------------------------------------------------------- render
render() {  # render <src> <dst> <mode>
  local src="$1" dst="$2" mode="$3" tmp
  tmp="$(mktemp)"
  sed -e "s|@SBIN_DIR@|${SBIN_DIR}|g" \
      -e "s|@CONFIG_DIR@|${CONFIG_DIR}|g" \
      -e "s|@CONFIG_FILE@|${CONFIG_FILE}|g" \
      -e "s|@DATA_DIR@|${DATA_DIR}|g" \
      -e "s|@BACKUP_DIR@|${BACKUP_DIR}|g" \
      -e "s|@LOG_FILE@|${LOG_FILE}|g" \
      -e "s|@LOCK_FILE@|${LOCK_FILE}|g" \
      -e "s|@RUN_USER@|${RUN_USER}|g" \
      -e "s|@RUN_GROUP@|${RUN_GROUP}|g" \
      -e "s|@PROJECT@|${PROJECT}|g" \
      -e "s|@NGINX_HOST@|${NGINX_HOST}|g" \
      -e "s|@NGINX_HTTP_PORT@|${NGINX_HTTP_PORT}|g" \
      -e "s|@CRON_SCHEDULE@|${CRON_SCHEDULE}|g" \
      -e "s|@CRON_MAILTO@|${CRON_MAILTO}|g" \
      -e "s|@SYSTEMD_ONCALENDAR@|${SYSTEMD_ONCALENDAR}|g" \
      -e "s|@GITHUB_SLUG@|${GITHUB_SLUG}|g" \
      "$src" > "$tmp"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry] render %s -> %s (mode %s)\n' "$(basename "$src")" "$dst" "$mode"
    rm -f "$tmp"; return 0
  fi
  install -D -m "$mode" "$tmp" "$dst"
  [ "$IS_ROOT" -eq 1 ] && chown "$RUN_USER:$RUN_GROUP" "$dst" 2>/dev/null || true
  rm -f "$tmp"
}

#----------------------------------------------------------------------- steps
create_dirs() {
  info "[1/6] Creating directories"
  run install -d -m 750 "$CONFIG_DIR" "$DATA_DIR" "$BACKUP_DIR" "$LOG_DIR"
  run install -d -m 755 "$SBIN_DIR"
  [ -d "$LOCK_DIR" ] || run install -d -m 755 "$LOCK_DIR"
  if [ "$IS_ROOT" -eq 1 ]; then
    run chown -R "$RUN_USER:$RUN_GROUP" "$DATA_DIR" "$CONFIG_DIR"
  fi
}

install_script() {
  info "[2/6] Installing script -> $SBIN_DIR/greenbone-update.sh"
  bash -n "$SRC_DIR/bin/greenbone-update.sh" || die "script syntax error"
  render "$SRC_DIR/bin/greenbone-update.sh" "$SBIN_DIR/greenbone-update.sh" 0750
}

install_config() {
  info "[3/6] Installing config -> $CONFIG_FILE"
  if [ -f "$CONFIG_FILE" ] && [ "$FORCE" -eq 0 ]; then
    warn "config already exists, keeping it (use --force to overwrite)"
    return 0
  fi
  [ -f "$CONFIG_FILE" ] && run cp -a "$CONFIG_FILE" "${CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
  render "$SRC_DIR/config/update.env.example" "$CONFIG_FILE" 0600
}

install_logrotate() {
  info "[4/6] logrotate"
  if [ "$IS_ROOT" -eq 1 ] && [ -d /etc/logrotate.d ]; then
    render "$SRC_DIR/logrotate/greenbone-update.in" /etc/logrotate.d/greenbone-update 0644
    [ "$DRY_RUN" -eq 1 ] || chown root:root /etc/logrotate.d/greenbone-update
  else
    warn "no logrotate.d / not root - the script will use its own internal rotation"
  fi
}

install_systemd() {
  info "[5/6] Installing systemd timer (OnCalendar: $SYSTEMD_ONCALENDAR)"
  render "$SRC_DIR/systemd/greenbone-update.service.in" /etc/systemd/system/greenbone-update.service 0644
  render "$SRC_DIR/systemd/greenbone-update.timer.in"   /etc/systemd/system/greenbone-update.timer   0644
  [ "$DRY_RUN" -eq 1 ] || chown root:root /etc/systemd/system/greenbone-update.{service,timer}
  run systemctl daemon-reload
  run systemctl enable --now greenbone-update.timer
}

install_cron() {
  info "[5/6] Installing cron ($CRON_SCHEDULE)"
  if [ "$IS_ROOT" -eq 1 ] && [ -d /etc/cron.d ]; then
    render "$SRC_DIR/cron/greenbone-update.cron.in" /etc/cron.d/greenbone-update 0644
    [ "$DRY_RUN" -eq 1 ] || chown root:root /etc/cron.d/greenbone-update
    info "    /etc/cron.d/greenbone-update"
  else
    command -v crontab >/dev/null 2>&1 || die "crontab not found, use --scheduler none"
    local marker="# greenbone-update (managed by install.sh)"
    local line="$CRON_SCHEDULE GB_CONFIG_FILE=$CONFIG_FILE $SBIN_DIR/greenbone-update.sh >/dev/null 2>&1"
    if [ "$DRY_RUN" -eq 1 ]; then
      printf '    [dry] crontab entry: %s\n' "$line"; return 0
    fi
    { crontab -l 2>/dev/null | grep -vF "$marker" | grep -vF "greenbone-update.sh" || true
      printf '%s\n%s\n' "$marker" "$line"
    } | crontab -
    info "    added to the user crontab ($RUN_USER)"
  fi
}

verify() {
  info "[6/6] Verification"
  if [ "$DRY_RUN" -eq 1 ]; then printf '    [dry] skipped\n'; return 0; fi
  [ -x "$SBIN_DIR/greenbone-update.sh" ] || die "script could not be installed"
  [ -r "$CONFIG_FILE" ] || die "config could not be installed"
  if grep -qE '@[A-Z_]+@' "$SBIN_DIR/greenbone-update.sh"; then
    warn "unprocessed placeholders left in the script: $(grep -oE '@[A-Z_]+@' "$SBIN_DIR/greenbone-update.sh" | sort -u | tr '\n' ' ')"
  fi
  case "$SCHEDULER" in
    systemd) systemctl is-enabled greenbone-update.timer >/dev/null 2>&1 \
               && info "    timer active: $(systemctl show -p NextElapseUSecRealtime --value greenbone-update.timer 2>/dev/null || echo '-')" \
               || warn "timer could not be enabled" ;;
    cron)    info "    cron entry written" ;;
    none)    warn "no scheduler installed (--scheduler none)" ;;
  esac
}

#------------------------------------------------------------------------ main
cat <<EOF
greenbone-update installer
  mode       : $([ "$IS_ROOT" -eq 1 ] && echo "root (system-wide)" || echo "user ($RUN_USER)")
  script     : $SBIN_DIR/greenbone-update.sh
  config     : $CONFIG_FILE
  data       : $DATA_DIR
  log        : $LOG_FILE
  lock       : $LOCK_FILE
  host:port  : $NGINX_HOST:$NGINX_HTTP_PORT
  scheduler  : $SCHEDULER ($([ "$SCHEDULER" = systemd ] && echo "$SYSTEMD_ONCALENDAR" || echo "$CRON_SCHEDULE"))
  dry-run    : $DRY_RUN
EOF

check_deps
create_dirs
install_script
install_config
install_logrotate
case "$SCHEDULER" in
  systemd) install_systemd ;;
  cron)    install_cron ;;
  none)    info "[5/6] scheduler skipped" ;;
esac
verify

cat <<EOF

Installation complete.

Next steps:
  1) Review the config      : $CONFIG_FILE
  2) Trial run              : ${IS_ROOT:+sudo }GB_DRY_RUN=1 $SBIN_DIR/greenbone-update.sh
  3) Real run               : ${IS_ROOT:+sudo }$SBIN_DIR/greenbone-update.sh
  4) Log                    : tail -f $LOG_FILE
EOF
