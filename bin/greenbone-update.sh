#!/usr/bin/env bash
#==============================================================================
# Greenbone Community Edition - scheduled update (software + feed)
#
# This file is rendered by install.sh (@...@ placeholders are filled in).
# Sensitive data (host/IP, path, port) is NOT KEPT HERE; it is read from the
# config file.
#
# Usage:
#   greenbone-update.sh                 # normal run
#   GB_DRY_RUN=1 greenbone-update.sh    # validate, do not deploy
#   GB_CONFIG_FILE=/path/update.env greenbone-update.sh
#
# Exit: 0 ok | 64 config | 69 preflight | 70 deploy+rollback | 75 lock
#==============================================================================
set -Eeuo pipefail
umask 027

#------------------------------------------------------------------ 0) config
CONFIG_FILE="${GB_CONFIG_FILE:-@CONFIG_FILE@}"
DRY_RUN="${GB_DRY_RUN:-0}"

case "$CONFIG_FILE" in
    *@*) echo "FATAL: script is not rendered. Run install.sh or set GB_CONFIG_FILE." >&2; exit 64 ;;
esac
if [ ! -r "$CONFIG_FILE" ]; then
    echo "FATAL: cannot read config: $CONFIG_FILE" >&2
    exit 64
fi
# shellcheck disable=SC1090
set -a; . "$CONFIG_FILE"; set +a

: "${DOWNLOAD_DIR:?missing in config: DOWNLOAD_DIR}"
: "${PROJECT:?missing in config: PROJECT}"
: "${NGINX_HOST:?missing in config: NGINX_HOST}"
: "${NGINX_HTTP_PORT:?missing in config: NGINX_HTTP_PORT}"

COMPOSE_URL="${COMPOSE_URL:-https://greenbone.github.io/docs/latest/_static/compose.yaml}"
COMPOSE_FILE="${COMPOSE_FILE:-$DOWNLOAD_DIR/compose.yaml}"
LOG="${LOG:-/var/log/greenbone-update.log}"
LOCK_FILE="${LOCK_FILE:-/var/lock/greenbone-update.lock}"
BACKUP_DIR="${BACKUP_DIR:-$DOWNLOAD_DIR/backups}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
MAX_IPS_PER_TARGET="${MAX_IPS_PER_TARGET:-66000}"
NOFILE_LIMIT="${NOFILE_LIMIT:-65536}"
NPROC_LIMIT="${NPROC_LIMIT:-65535}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-600}"
MIN_FREE_MB="${MIN_FREE_MB:-15000}"
CURL_RETRY="${CURL_RETRY:-3}"
PRUNE_ENABLED="${PRUNE_ENABLED:-1}"
PRUNE_UNTIL="${PRUNE_UNTIL:-168h}"
MAX_LOG_BYTES="${MAX_LOG_BYTES:-20971520}"
STRICT_PATCH="${STRICT_PATCH:-1}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${NGINX_HTTP_PORT}/}"
DOCKER_BIN="${DOCKER_BIN:-docker}"

#--------------------------------------------------------------- 1) log/util
STAGE="init"; BACKUP_FILE=""; TMP_FILE=""; DEPLOYED=0; ROLLED_BACK=0; HAVE_YQ=0

redact() {
    sed -e "s#${NGINX_HOST}#<NGINX_HOST>#g" \
    -e "s#${DOWNLOAD_DIR}#<DOWNLOAD_DIR>#g" \
    -e 's#\(password\|passwd\|secret\|token\|api[_-]\?key\)\([":= ]\+\)[^ "]\+#\1\2<REDACTED>#Ig'
}

_write() { printf '%s\n' "$*" | redact >> "$LOG"; }
log()  { local m="[$(date +%H:%M:%S)] $*"; printf '%s\n' "$m" | redact; _write "$m"; }
warn() { log "WARN: $*"; }
die()  { local c="${2:-1}"; log "FATAL [$STAGE]: $1"; exit "$c"; }

run() {
    local rc=0
    { "$@" 2>&1 || rc=$?; } | redact | tee -a "$LOG"
    return "$rc"
}

rotate_log() {
    install -d -m 750 "$(dirname "$LOG")" 2>/dev/null || true
    [ -e "$LOG" ] || : > "$LOG"
    chmod 640 "$LOG" 2>/dev/null || true
    local sz; sz=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
    if [ "$sz" -gt "$MAX_LOG_BYTES" ]; then mv -f "$LOG" "${LOG}.1"; : > "$LOG"; fi
    return 0
}

on_err() {
    local rc=$? line=$1
    log "ERROR: line ${line}, exit=${rc}, stage=${STAGE}"
    if [ "$DEPLOYED" -eq 1 ] && [ "$ROLLED_BACK" -eq 0 ]; then rollback; fi
    exit "$rc"
}
cleanup() { [ -n "$TMP_FILE" ] && [ -f "$TMP_FILE" ] && rm -f "$TMP_FILE"; return 0; }
trap 'on_err $LINENO' ERR
trap cleanup EXIT

#------------------------------------------------------------------ 2) preflight
preflight() {
    STAGE="preflight"
    local c
    for c in "$DOCKER_BIN" curl sed stat flock awk df; do
        command -v "$c" >/dev/null 2>&1 || die "command not found: $c" 69
    done
    "$DOCKER_BIN" compose version >/dev/null 2>&1 || die "docker compose v2 plugin not found" 69
    "$DOCKER_BIN" info >/dev/null 2>&1 || die "docker daemon unreachable (permissions/socket?)" 69
    
    install -d -m 750 "$DOWNLOAD_DIR" "$BACKUP_DIR" || die "could not create directory" 69
    install -d -m 755 "$(dirname "$LOCK_FILE")" 2>/dev/null || true
    
    local root free
    root=$("$DOCKER_BIN" info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
    free=$(df -Pm "$root" 2>/dev/null | awk 'NR==2{print $4}')
    [ "${free:-0}" -ge "$MIN_FREE_MB" ] || die "insufficient disk space: ${free}MB < ${MIN_FREE_MB}MB (${root})" 69
    
    command -v yq >/dev/null 2>&1 && HAVE_YQ=1 || HAVE_YQ=0
    log "preflight ok (disk=${free}MB, yq=${HAVE_YQ}, dry_run=${DRY_RUN})"
}

#---------------------------------------------------- 3) fetch + backup + patch
backup_compose() {
    STAGE="backup"
    if [ -f "$COMPOSE_FILE" ]; then
        BACKUP_FILE="$BACKUP_DIR/compose.yaml.$(date +%Y%m%d-%H%M%S)"
        cp -a "$COMPOSE_FILE" "$BACKUP_FILE"
        log "backup created: $(basename "$BACKUP_FILE")"
        ls -1t "$BACKUP_DIR"/compose.yaml.* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f
    else
        log "no existing compose.yaml, first install"
    fi
}

fetch_compose() {
    STAGE="fetch"
    TMP_FILE=$(mktemp "${DOWNLOAD_DIR}/.compose.XXXXXX")
    curl -fsSL --retry "$CURL_RETRY" --retry-delay 5 --retry-connrefused \
    --connect-timeout 10 --max-time 120 "$COMPOSE_URL" -o "$TMP_FILE" \
    || die "could not download compose.yaml" 69
    [ -s "$TMP_FILE" ] || die "downloaded compose.yaml is empty" 69
    grep -q '^services:' "$TMP_FILE" || die "downloaded file is not a valid compose file" 69
    log "compose.yaml downloaded ($(stat -c%s "$TMP_FILE") bytes)"
}

patch_yq() {
    GVMD_ARGS_VAL="-f --listen-mode=666 --max-ips-per-target=${MAX_IPS_PER_TARGET}"
    export GVMD_ARGS_VAL NGINX_HOST NGINX_HTTP_PORT NOFILE_LIMIT NPROC_LIMIT
    
    yq -i '(.services[] | select(.environment.ENABLE_TLS_GENERATION != null).environment)
    |= (.NGINX_HOST = strenv(NGINX_HOST) | .NGINX_HTTP_PORT = env(NGINX_HTTP_PORT))' "$TMP_FILE"
    
    yq -i '(.services[] | select(.environment.ENABLE_TLS_GENERATION != null).ports)
    |= ["443:443", (env(NGINX_HTTP_PORT) + ":" + env(NGINX_HTTP_PORT))]' "$TMP_FILE"
    
    yq -i '(.services[] | select((.image // "") | test("community/gvmd")).environment.GVMD_ARGS)
    = strenv(GVMD_ARGS_VAL)' "$TMP_FILE"
    
    yq -i '(.services[] | select((.image // "") | test("community/(redis-server|pg-gvm|ospd-openvas)")).ulimits)
    = {"nofile": {"soft": env(NOFILE_LIMIT), "hard": env(NOFILE_LIMIT)}, "nproc": env(NPROC_LIMIT)}' "$TMP_FILE"
}

patch_sed() {
    grep -q 'ENABLE_TLS_GENERATION' "$TMP_FILE" || die "anchor not found: ENABLE_TLS_GENERATION" 69
    
    if ! grep -q 'NGINX_HOST' "$TMP_FILE"; then
        sed -i -e "/ENABLE_TLS_GENERATION: true/a\\      NGINX_HOST: \"${NGINX_HOST}\"\\n      NGINX_HTTP_PORT: ${NGINX_HTTP_PORT}" "$TMP_FILE"
    fi
    
    sed -i -e 's/- 127\.0\.0\.1:443:443/- 443:443/' \
    -e "s/- 127\.0\.0\.1:9392:9392/- ${NGINX_HTTP_PORT}:${NGINX_HTTP_PORT}/" "$TMP_FILE"
    
    if ! grep -q 'max-ips-per-target' "$TMP_FILE"; then
        grep -q 'community/gvmd' "$TMP_FILE" || die "anchor not found: gvmd image" 69
        sed -i -e "/community\/gvmd:stable/a\\    environment:\n      GVMD_ARGS: \"-f --listen-mode=666 --max-ips-per-target=${MAX_IPS_PER_TARGET}\"" "$TMP_FILE"
    fi
    
    if ! grep -q 'ulimits' "$TMP_FILE"; then
        local U="    ulimits:\n      nofile:\n        soft: ${NOFILE_LIMIT}\n        hard: ${NOFILE_LIMIT}\n      nproc: ${NPROC_LIMIT}"
        sed -i -e "/image: registry\.community\.greenbone\.net\/community\/redis-server/a\\$U" \
        -e "/image: registry\.community\.greenbone\.net\/community\/pg-gvm:stable/a\\$U" \
        -e "/image: registry\.community\.greenbone\.net\/community\/ospd-openvas:stable/a\\$U" "$TMP_FILE"
    fi
}

# Verify the patches were ACTUALLY applied (catches upstream service/key renames)
assert_patches() {
    local missing=""
    grep -q "NGINX_HOST" "$TMP_FILE"                        || missing="${missing} NGINX_HOST"
    grep -qE "NGINX_HTTP_PORT" "$TMP_FILE"                  || missing="${missing} NGINX_HTTP_PORT"
    grep -q "max-ips-per-target=${MAX_IPS_PER_TARGET}" "$TMP_FILE" || missing="${missing} GVMD_ARGS"
    grep -q "ulimits" "$TMP_FILE"                           || missing="${missing} ulimits"
    grep -q "127.0.0.1:9392" "$TMP_FILE"                    && missing="${missing} ports(loopback-remains)"
    if [ -n "$missing" ]; then
        if [ "$STRICT_PATCH" = "1" ]; then
            die "patch validation failed:${missing} (upstream compose may have changed)" 69
        fi
        warn "patch validation incomplete:${missing} (STRICT_PATCH=0, continuing)"
    else
        log "patch validation ok"
    fi
}

patch_compose() {
    STAGE="patch"
    if [ "$HAVE_YQ" -eq 1 ]; then patch_yq; log "patch applied (yq)"
else patch_sed; log "patch applied (sed fallback)"; fi
    assert_patches
}

validate_compose() {
    STAGE="validate"
    "$DOCKER_BIN" compose -f "$TMP_FILE" -p "$PROJECT" config -q \
    || die "invalid compose.yaml, no changes applied" 69
    local n; n=$("$DOCKER_BIN" compose -f "$TMP_FILE" -p "$PROJECT" config --services | wc -l)
    [ "$n" -ge 5 ] || die "fewer services than expected: ${n}" 69
    log "compose validated (${n} services)"
}

commit_compose() {
    STAGE="commit"
    install -m 640 "$TMP_FILE" "$COMPOSE_FILE"
    rm -f "$TMP_FILE"; TMP_FILE=""
    log "compose.yaml updated"
}

#-------------------------------------------------------------------- 4) deploy
dc() { "$DOCKER_BIN" compose -f "$COMPOSE_FILE" -p "$PROJECT" "$@"; }

pull_images() {
    STAGE="pull"
    run dc pull || die "image pull failed, stack left unchanged" 69
    log "pull done"
}

deploy() {
    STAGE="deploy"
    DEPLOYED=1
    run dc up -d --remove-orphans || { warn "up -d failed"; return 1; }
    log "up -d done"
}

health_check() {
    STAGE="health"
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) code=""
    while [ "$SECONDS" -lt "$deadline" ]; do
        if dc ps --status running --format '{{.Service}}' 2>/dev/null | grep -q .; then
            code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" || echo 000)
            case "$code" in
                200|301|302|401|403) log "health ok (HTTP ${code})"; return 0 ;;
            esac
        fi
        sleep 10
    done
    warn "health timeout (${HEALTH_TIMEOUT}s, last code=${code:-none})"
    return 1
}

feed_status() {
    STAGE="feed"
    local bad
    bad=$(dc ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' 2>/dev/null \
    | awk '$2=="exited" && $3!=0 {print $1"(rc="$3")"}' | paste -sd, - || true)
    if [ -n "${bad:-}" ]; then warn "feed/init containers exited with errors: ${bad}"
else log "feed/init containers ok"; fi
}

rollback() {
    ROLLED_BACK=1; STAGE="rollback"
    if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
        warn "no backup available, automatic rollback not possible - manual intervention required"; return 0
    fi
    log "ROLLBACK: restoring previous compose.yaml"
    install -m 640 "$BACKUP_FILE" "$COMPOSE_FILE"
    run dc up -d --remove-orphans || warn "up -d failed after rollback"
    if health_check; then log "service is up after rollback"
else warn "health check failed after rollback - manual intervention required"; fi
}

prune() {
    STAGE="prune"
    [ "$PRUNE_ENABLED" = "1" ] || { log "prune disabled"; return 0; }
    run "$DOCKER_BIN" image prune -f --filter "dangling=true" --filter "until=${PRUNE_UNTIL}" \
    || warn "prune failed (non-fatal)"
    log "prune done"
}

#---------------------------------------------------------------------- 5) main
main() {
    rotate_log
    log "===== Greenbone update started: $(date -Iseconds) ====="
    preflight
    backup_compose
    fetch_compose
    patch_compose
    validate_compose
    
    if [ "$DRY_RUN" = "1" ]; then
        log "DRY RUN: validation succeeded, nothing deployed"
        log "===== Greenbone update finished (dry-run): $(date -Iseconds) ====="
        return 0
    fi
    
    commit_compose
    pull_images
    
    if ! deploy || ! health_check; then
        rollback
        die "deploy/health check failed, rollback executed" 70
    fi
    
    feed_status
    prune
    log "===== Greenbone update finished OK: $(date -Iseconds) ====="
}

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Already running (lock: $LOCK_FILE)" >&2; exit 75; }

main "$@"
