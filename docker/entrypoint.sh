#!/bin/sh
set -eu

data_dir="${SHIXUE_DATA_DIR:-/data}"
mkdir -p "$data_dir"

# First boot only: create the portal administrator from the environment.
admin_marker="$data_dir/.admin-initialized"
if [ ! -f "$admin_marker" ] && [ -n "${SHIXUE_PORTAL_ADMIN_PASSWORD:-}" ]; then
    python -m backend.portal \
        --data-dir "$data_dir" \
        --init-admin "${SHIXUE_PORTAL_ADMIN_USERNAME:-admin}" || true
    touch "$admin_marker"
fi

python -m backend.portal \
    --data-dir "$data_dir" \
    --ui-dir /app/dist \
    --host 0.0.0.0 \
    --port "${SHIXUE_PORTAL_PORT:-8765}" &
portal_pid=$!

shutdown() {
    python -m backend.portal --data-dir "$data_dir" --stop-workspaces >/dev/null 2>&1 || true
    kill -TERM "$portal_pid" 2>/dev/null || true
    wait "$portal_pid" 2>/dev/null || true
    exit 0
}
trap shutdown INT TERM

wait "$portal_pid"
