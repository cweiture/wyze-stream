#!/usr/bin/env bash
# Compatibility entry point. Configuration and lifecycle live in Python.
set -euo pipefail
exec python3 "$(dirname -- "${BASH_SOURCE[0]}")/wyze_stream.py" relay "$@"
