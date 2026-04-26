#!/bin/bash
# Lancy — Batch upload documents to a remote backend
#
# Usage:
#   ./scripts/upload-docs.sh <backend-url> <kb-id> <directory>
#
# Example:
#   ./scripts/upload-docs.sh http://192.168.1.141:8080 default ~/my-docs/
#
# Uploads all supported files (.pdf .md .txt .png .jpg .jpeg .gif .tiff .bmp .webp)
# from <directory> (recursive up to 5 levels deep). document_id is set to the filename stem.
# Waits for each file to finish ingesting before uploading the next.

set -e

BACKEND_URL="${1:-}"
KB_ID="${2:-}"
DOC_DIR="${3:-}"

if [ -z "$BACKEND_URL" ] || [ -z "$KB_ID" ] || [ -z "$DOC_DIR" ]; then
    echo "Usage: $0 <backend-url> <kb-id> <directory>"
    echo "  e.g. $0 http://192.168.1.141:8080 default ~/my-docs/"
    exit 1
fi

if [ ! -d "$DOC_DIR" ]; then
    echo "ERROR: '$DOC_DIR' is not a directory."
    exit 1
fi

SUPPORTED_EXTS="pdf md txt png jpg jpeg gif tiff bmp webp"
ENDPOINT="$BACKEND_URL/api/v1/kb/$KB_ID/documents"
STATUS_URL="$BACKEND_URL/api/v1/rag/reindex-status"
WAIT_TIMEOUT=2400  # max seconds to wait for a file to finish indexing

# Check backend is reachable before doing anything
echo "Checking backend at $BACKEND_URL ..."
if ! curl -sf --max-time 5 "$STATUS_URL" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach backend at $BACKEND_URL"
    echo "  Check the URL and make sure the server is running."
    exit 1
fi
echo "Backend OK."
echo ""

wait_for_idle() {
    local elapsed=0
    local fail_streak=0
    while true; do
        local response
        response=$(curl -sf --max-time 5 "$STATUS_URL" 2>/dev/null) || response=""
        if [ -z "$response" ]; then
            fail_streak=$((fail_streak + 1))
            if [ "$fail_streak" -ge 3 ]; then
                echo ""
                echo "ERROR: Lost connection to backend (3 consecutive failures)."
                exit 1
            fi
            sleep 10
            elapsed=$((elapsed + 10))
            continue
        fi
        fail_streak=0
        local indexing
        indexing=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('indexing', False))" 2>/dev/null)
        [ "$indexing" = "False" ] && break
        printf "."
        sleep 10
        elapsed=$((elapsed + 10))
        if [ "$elapsed" -ge "$WAIT_TIMEOUT" ]; then
            echo ""
            echo "ERROR: Timed out after ${WAIT_TIMEOUT}s waiting for indexing to finish."
            exit 1
        fi
    done
}

# Collect matching files (recursive, max 5 levels deep)
files=()
for ext in $SUPPORTED_EXTS; do
    while IFS= read -r -d '' f; do
        files+=("$f")
    done < <(find "$DOC_DIR" -maxdepth 5 -type f -iname "*.${ext}" -print0 2>/dev/null)
done

total=${#files[@]}
if [ "$total" -eq 0 ]; then
    echo "No supported files found in '$DOC_DIR'."
    exit 0
fi

echo "Uploading $total file(s) to $ENDPOINT (KB: $KB_ID)"
echo ""

success=0
failed=0

for file in "${files[@]}"; do
    filename="$(basename "$file")"
    # Show path relative to DOC_DIR for files in subdirectories
    relpath="${file#$DOC_DIR/}"
    doc_id="${filename%.*}"

    printf "  [%d/%d] %s ... " "$((success + failed + 1))" "$total" "$relpath"

    metadata=$(python3 -c "import json,sys; print(json.dumps({'document_id': sys.argv[1], 'source_file': sys.argv[2]}))" "$doc_id" "$filename")
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$ENDPOINT" \
        -F "file=@\"${file}\";filename=\"${filename}\"" \
        -F "metadata=${metadata}")

    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        printf "uploaded, indexing"
        wait_for_idle
        echo " done"
        ((success++)) || true
    else
        echo "FAILED (HTTP $http_code)"
        ((failed++)) || true
    fi
done

echo ""
echo "Done: $success uploaded, $failed failed."
if [ "$failed" -gt 0 ]; then
    echo "Check backend logs for details: logs/backend.log"
fi
