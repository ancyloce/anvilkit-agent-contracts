#!/bin/sh
# Reproducible entry point for the verification of anvilkit-agent-contracts.
#
#   sh tools/verification-env.sh [verify.py arguments]
#
# Creates (or reuses) .local/verification-venv from CPython 3.12 with the
# pinned tools of tools/requirements.txt, installs the Python consumer package
# (python/) in place, then runs tools/verify.py under that interpreter with the
# given arguments (for example --only check --only generate). The venv lives
# under the ignored .local/ directory; nothing else on the machine is changed.
# The Go, Node/pnpm (packageManager in ts/package.json) and buf/oapi-codegen
# pins are checked by the tools themselves.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENV="$ROOT/.local/verification-venv"
PY=${ANVILKIT_PYTHON:-}
if [ -z "$PY" ]; then
  for candidate in python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
      PY=$(command -v "$candidate")
      break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "FAIL: CPython 3.12 not found (set ANVILKIT_PYTHON to a 3.12 interpreter)" >&2
  exit 2
fi
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --require-virtualenv -r "$ROOT/tools/requirements.txt"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --require-virtualenv --no-deps -e "$ROOT/python"
exec "$VENV/bin/python" "$ROOT/tools/verify.py" "$@"
