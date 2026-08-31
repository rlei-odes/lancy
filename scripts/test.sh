#!/bin/bash
# Lancy — Run the whole test suite (Python + frontend).
#
# Both halves always run: a red frontend must not hide a red backend, so the
# exit code is only 0 when both passed.
#
#   scripts/test.sh            both suites
#   scripts/test.sh py         Python only
#   scripts/test.sh js         frontend only
#   scripts/test.sh -k presets extra args go to whichever suites run

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO/.venv/bin/python"

WHICH="both"
case "${1:-}" in
    py|python) WHICH="py"; shift ;;
    js|ts|frontend) WHICH="js"; shift ;;
esac

py_status="skipped"
js_status="skipped"

if [ "$WHICH" = "both" ] || [ "$WHICH" = "py" ]; then
    echo "─── Python ──────────────────────────────────────────────────────────"
    if [ ! -x "$VENV_PY" ]; then
        echo "ERROR: no interpreter at $VENV_PY — run scripts/install-backend.sh first."
        py_status="error"
    else
        # cwd matters: pytest.ini at the repo root defines testpaths.
        (cd "$REPO" && "$VENV_PY" -m pytest "$@")
        [ $? -eq 0 ] && py_status="passed" || py_status="FAILED"
    fi
    echo
fi

if [ "$WHICH" = "both" ] || [ "$WHICH" = "js" ]; then
    echo "─── Frontend ────────────────────────────────────────────────────────"
    if [ ! -d "$REPO/frontend/node_modules" ]; then
        echo "ERROR: frontend/node_modules missing — run npm install in frontend/ first."
        js_status="error"
    else
        (cd "$REPO/frontend" && npx vitest run "$@")
        [ $? -eq 0 ] && js_status="passed" || js_status="FAILED"
    fi
    echo
fi

echo "─── Summary ─────────────────────────────────────────────────────────"
printf "  Python    %s\n" "$py_status"
printf "  Frontend  %s\n" "$js_status"

case "$py_status $js_status" in
    *FAILED*|*error*) exit 1 ;;
    *) exit 0 ;;
esac
