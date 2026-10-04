#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Foreground, self-logging commands. Each emits its final JSON once.
dashboard_args=()
if [[ -n "${DSBENCH_DASHBOARD_URL:-}" ]]; then
  dashboard_args=(--dashboard-url "$DSBENCH_DASHBOARD_URL")
fi
uv run --locked --no-sync bench doctor --runtime --containers "${dashboard_args[@]}"
uv run --locked --no-sync bench run --output outputs/experiment "${dashboard_args[@]}"
uv run --locked --no-sync bench report --output outputs/experiment
if [[ -n "${DSBENCH_DASHBOARD_URL:-}" ]]; then
  uv run --locked --no-sync bench sync-dashboard --output outputs/experiment --dashboard-url "$DSBENCH_DASHBOARD_URL"
fi
