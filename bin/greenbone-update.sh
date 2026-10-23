#!/usr/bin/env bash
# Rendered entry point. No implicit software or feed updates.
set -Eeuo pipefail
CONFIG_FILE="${GB_CONFIG_FILE:-@CONFIG_FILE@}"
LIB_DIR="@LIB_DIR@"
if [[ "$LIB_DIR" == *@* ]]; then
    LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
fi
exec python3 "$LIB_DIR/maintenance.py" --config "$CONFIG_FILE" "$@"
