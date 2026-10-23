#!/usr/bin/env bash
# Shared by repository installer/uninstaller, never sourced from runtime config.
acquire_tooling_locks() {
    local config="$1" default_lock="$2" old_lock='' path fd
    [[ "$DRY_RUN" == 0 ]] || { printf '[dry-run] acquire runtime maintenance locks\n'; return; }
    command -v flock >/dev/null || { printf 'ERROR: flock is required\n' >&2; exit 64; }
    if [[ -f "$config" ]]; then
        old_lock=$(python3 - "$config" <<'PY'
import pathlib, shlex, sys
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    if line.startswith('LOCK_FILE='):
        value = shlex.split(line.split('=', 1)[1], comments=True)
        if len(value) != 1 or any(c in value[0] for c in ('$','`','\n')):
            sys.exit(64)
        print(value[0])
        break
PY
        )
    fi
    TOOL_LOCK_FDS=()
    TOOL_LOCK_PATH=''
    for path in "${old_lock:-$default_lock}" "$default_lock"; do
        [[ "$path" == /* && "$path" != / && ! -L "$path" ]] || { printf 'ERROR: Unsafe maintenance lock\n' >&2; exit 64; }
        [[ -z "${TOOL_LOCK_PATH:-}" || "$path" != "$TOOL_LOCK_PATH" ]] || continue
        mkdir -p "$(dirname "$path")"
        exec {fd}>>"$path"
        if ! flock -n "$fd"; then
            printf 'ERROR: Maintenance is active. Schedules disabled; wait for completion and retry.\n' >&2
            exit 75
        fi
        TOOL_LOCK_FDS+=("$fd")
        TOOL_LOCK_PATH="$path"
        chmod 0600 "$path"
        if [[ "$(id -u)" == 0 ]]; then chown "$RUN_USER" "$path"; fi
    done
}
