#!/usr/bin/env bash
# Run the AudioHelper test suite using the project venv.
#   bash run_tests.sh           # all tests
#   bash run_tests.sh -k parser # filter by name
set -eo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/.venv/bin/python3"
[[ -x "$PY" ]] || PY="python3"
"$PY" -m pip install pytest --quiet 2>/dev/null || true
exec "$PY" -m pytest "$@"
