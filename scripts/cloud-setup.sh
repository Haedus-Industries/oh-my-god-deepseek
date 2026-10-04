#!/usr/bin/env bash
set -euo pipefail
# No API key is read or persisted. Do not use setup-only secrets for paid runs.
cd "$(dirname "$0")/.."
uv sync --locked --extra cloud --extra analysis --group dev
uv run --locked --no-sync bench prepare --images
uv run --locked --no-sync bench doctor
