#!/usr/bin/env python3
"""Lancy — Batch document analysis.

Runs POST /api/v1/rag/analyze-document once per document in a list, collects the
structured JSON results into a CSV that can be opened as a table.

Each doc's full text (all chunks) is fed to the main LLM together with the
questions from --prompt and the schema from --schema. The LLM answers strictly
in JSON matching the schema; each answer becomes one CSV row.

Prompt and schema must line up:
    - The prompt asks natural-language questions ("Q1: does this doc mention X?").
    - The schema declares one field per answer ("question1": {"type": "string"}).
    - The backend prompt template (prompts/batch_analyze.default.md) shows both
      the prompt and the schema to the LLM, so keep the field names and question
      order aligned or the LLM won't know which answer belongs where.

Ready-to-adapt examples ship in scripts/examples/batch-analyze/.

Usage:
    ./scripts/batch-analyze.py \\
        --frontend http://localhost:3000 \\
        --files docs.txt \\
        --prompt prompt.txt \\
        --schema schema.json \\
        --output results.csv

Optional:
    --kb <kb_id>          Target a specific KB (default: currently active KB).
    --id-field ID_FIELD   Which metadata field to match entries in docs.txt against.
                          Every chunk carries BOTH of these — pick whichever you have.
                            'source_file' (default) — the ingested filename, e.g.
                              'contract-2025-01.pdf'. Right when you uploaded via
                              upload-docs.sh from a local folder.
                            'document_id' — the stable id set at upload time.
                              upload-docs.sh sets this to the filename stem
                              ('contract-2025-01'); a DMS integration would use its
                              own record id ('CTR-2025-001'). Right when your batch
                              is driven by DMS ids you want to join back to.
    --password PASSWORD   Login password; else $LANCY_PASSWORD; else interactive.
    --force               Re-run ids already in the output CSV instead of skipping.
    --interactive         Step through the preflight checks one at a time, confirming
                          each with Enter before the run starts. Recommended for a
                          first run against an unfamiliar KB.
    --dry-run             Run the preflight checks and exit without analyzing anything.

Preflight checks (always run; --interactive pauses between them):
    1. Inputs      — all three files present, prompt non-empty, schema well-formed.
                     'required' must not name fields missing from 'properties', and on
                     OpenAI-compatible backends strict mode wants every property listed
                     in 'required' — both are reported here rather than as an HTTP 502
                     once per document.
    2. Duplicates  — repeated identifiers are collapsed (first occurrence wins). Each
                     duplicate would otherwise be a full, redundant LLM call.
    3. KB match    — reports the backend/model, the per-document char budget derived
                     from num_ctx, and how many identifiers actually exist in the target
                     KB. Catches a wrong --kb or --id-field before the run, not after.

Input files:
    docs.txt     One identifier per line. Interpreted per --id-field.
                 Blank lines and lines starting with '#' are ignored.
    prompt.txt   Natural-language questions for the LLM. Free-form text; keep
                 question numbering aligned with the schema field names.
    schema.json  JSON Schema for the LLM output. The schema's top-level
                 'properties' become the CSV columns (order preserved).

Output CSV columns:
    _id, _status, _chunk_count, _char_count, <field1>, <field2>, ...

    _status is one of:
      'ok'          — LLM returned a parsed result
      'no_chunks'   — identifier not found in the KB
      'over_budget' — doc exceeds ~60% of num_ctx, skipped (see backend logs
                      for budget_chars; increase num_ctx in the RAG panel or
                      pre-summarise the doc if you want it in scope)
      'error:<msg>' — network, HTTP, or LLM error

    Rows are appended one at a time — safe to interrupt and re-run (already-
    processed ids are skipped unless --force is passed).

Auth:
    The script POSTs to /api/auth/login on the frontend, which sets an
    HttpOnly 'rag_auth' cookie carried on subsequent /api/v1/* calls.
    Password source (in priority order):
      1. --password on the command line
      2. LANCY_PASSWORD env var
      3. Interactive prompt
    LDAP mode (Mode 3) is not supported here — use the admin escape hatch by
    setting LANCY_ADMIN=1 to send {password, adminEscape: true}.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("This script needs 'requests'. Install with: pip install requests")


def login(session: requests.Session, frontend: str, password: str, admin_escape: bool) -> None:
    payload: dict = {"password": password}
    if admin_escape:
        payload["adminEscape"] = True
    try:
        r = session.post(f"{frontend}/api/auth/login", json=payload, timeout=15)
    except requests.exceptions.SSLError as exc:
        # Self-signed / internal CA: requests uses certifi, not the OS trust store,
        # so a cert the browser accepts without complaint still fails here.
        sys.exit(
            f"TLS verification failed for {frontend}:\n  {exc}\n\n"
            "The browser trusting this host is not enough - requests uses its own CA\n"
            "bundle. Point it at a bundle that includes your internal root CA:\n"
            '  PowerShell:  $env:REQUESTS_CA_BUNDLE = "C:\\path\\to\\ca-bundle.pem"\n'
            "  bash:        export REQUESTS_CA_BUNDLE=/path/to/ca-bundle.pem"
        )
    except requests.RequestException as exc:
        sys.exit(f"Could not reach {frontend}:\n  {exc}")
    if r.status_code != 200:
        hint = ""
        if r.status_code == 400 and "sername" in r.text:
            # Mode 3 (LDAP): the login route only falls through to the password
            # branch when adminEscape is set.
            hint = ("\n  This deployment uses LDAP login. To use the APP_PASSWORD or\n"
                    "  ADMIN_PASSWORD instead, set LANCY_ADMIN=1 for the admin escape hatch.")
        sys.exit(f"Login failed ({r.status_code}): {r.text.strip()}{hint}")
    role = r.json().get("role", "?")
    print(f"  logged in as role={role}")


def read_id_list(path: Path) -> tuple[list[str], int]:
    """Read identifiers, dropping blanks, comments and repeats.

    Returns (unique ids in first-seen order, number of duplicate lines dropped).
    Deduplication matters: the analysis is per-document and each request is a full
    LLM call, so a repeated identifier is pure wasted time — nothing downstream
    would merge or notice them.
    """
    ids: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s in seen:
            duplicates += 1
            continue
        seen.add(s)
        ids.append(s)
    if not ids:
        sys.exit(f"No identifiers in {path}")
    return ids, duplicates


def validate_schema(schema: dict) -> tuple[list[str], list[str]]:
    """Check a response schema for problems that would fail at request time.

    Returns (errors, warnings). Errors make the request invalid; warnings only bite
    on backends that enforce the schema at decode time (custom / vLLM / LiteLLM).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if schema.get("type") != "object":
        errors.append(f"top-level 'type' should be 'object', found {schema.get('type')!r}")
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        errors.append("no 'properties' object - there would be no CSV columns to fill")
        return errors, warnings

    required = schema.get("required", [])
    if not isinstance(required, list):
        errors.append("'required' must be a list")
        return errors, warnings

    # A required field with no definition is rejected outright by strict decoding.
    for field in required:
        if field not in props:
            errors.append(f"'required' names {field!r}, which is not in 'properties'")
    # OpenAI strict mode additionally demands that every property be required.
    missing = [k for k in props if k not in required]
    if missing:
        warnings.append(
            "not in 'required': " + ", ".join(missing)
            + " - OpenAI-compatible backends reject this under strict mode"
        )
    return errors, warnings


def confirm(next_step: str, interactive: bool) -> None:
    if not interactive:
        return
    try:
        input(f"\n  Next: {next_step}\n  Enter to continue, Ctrl-C to abort... ")
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nAborted.")


def fetch_config(session: requests.Session, frontend: str) -> dict:
    try:
        r = session.get(f"{frontend}/api/v1/rag/config", timeout=15)
        return r.json() if r.status_code == 200 else {}
    except requests.RequestException:
        return {}


def fetch_ingested_files(session: requests.Session, frontend: str, kb_id: str | None) -> set[str] | None:
    """source_file values present in the target KB, or None if unavailable.

    None means "could not check" — distinct from an empty set, which means the KB
    genuinely has no documents.
    """
    params = {"kb_id": kb_id} if kb_id else {}
    try:
        r = session.get(f"{frontend}/api/v1/rag/store-info", params=params, timeout=120)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return set(r.json().get("file_list") or [])


def load_processed(output: Path) -> set[str]:
    if not output.exists():
        return set()
    with output.open() as f:
        return {row["_id"] for row in csv.DictReader(f) if row.get("_id")}


def analyze_one(
    session: requests.Session, frontend: str, id_field: str, ident: str,
    prompt: str, schema: dict, kb_id: str | None,
) -> dict:
    body: dict = {id_field: ident, "prompt": prompt, "response_schema": schema}
    if kb_id:
        body["kb_id"] = kb_id
    try:
        r = session.post(
            f"{frontend}/api/v1/rag/analyze-document",
            json=body,
            timeout=125,  # backend times out at 110s; give the proxy a few extra seconds
        )
    except requests.RequestException as exc:
        return {"_status": f"error:{exc}", "_chunk_count": 0, "_char_count": 0}

    if r.status_code != 200:
        detail = r.text.strip()[:200]
        return {"_status": f"error:HTTP {r.status_code} {detail}", "_chunk_count": 0, "_char_count": 0}

    data = r.json()
    if data.get("skipped"):
        return {
            "_status": data["skipped"],
            "_chunk_count": data.get("chunk_count", 0),
            "_char_count": data.get("char_count", 0),
        }
    row: dict = {
        "_status": "ok",
        "_chunk_count": data.get("chunk_count", 0),
        "_char_count": data.get("char_count", 0),
    }
    for k, v in (data.get("result") or {}).items():
        row[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch-analyze documents in a Lancy KB.")
    ap.add_argument("--frontend", default=os.getenv("LANCY_FRONTEND", "http://localhost:3000"),
                    help="Frontend base URL (default: http://localhost:3000).")
    ap.add_argument("--files", required=True, type=Path,
                    help="Path to a text file listing one identifier per line.")
    ap.add_argument("--prompt", required=True, type=Path,
                    help="Path to a text file with the analysis prompt.")
    ap.add_argument("--schema", required=True, type=Path,
                    help="Path to a JSON Schema file for the LLM output.")
    ap.add_argument("--output", required=True, type=Path,
                    help="CSV output path. Appended row-by-row; safe to resume.")
    ap.add_argument("--id-field", choices=["source_file", "document_id"], default="source_file",
                    help="How to interpret entries in --files (default: source_file).")
    ap.add_argument("--kb", default=None,
                    help="KB id to target. Defaults to the currently active KB.")
    ap.add_argument("--password", default=None,
                    help="Login password. Falls back to $LANCY_PASSWORD, then interactive prompt.")
    ap.add_argument("--force", action="store_true",
                    help="Re-analyze ids already present in the output CSV.")
    ap.add_argument("--interactive", action="store_true",
                    help="Pause for confirmation between preflight checks.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run the preflight checks and exit without analyzing.")
    args = ap.parse_args()

    frontend = args.frontend.rstrip("/")

    # ── 1. Inputs ────────────────────────────────────────────────────────────
    print("[1/3] Checking inputs")
    for label, path in (("--files", args.files), ("--prompt", args.prompt), ("--schema", args.schema)):
        if not path.exists():
            sys.exit(f"  {label}: {path} does not exist.")

    prompt = args.prompt.read_text(encoding="utf-8").strip()
    if not prompt:
        sys.exit(f"  --prompt: {args.prompt} is empty.")
    try:
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"  --schema: {args.schema} is not valid JSON - {exc}")

    errors, warnings = validate_schema(schema)
    for w in warnings:
        print(f"  WARNING: {w}")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        sys.exit("  Fix the schema before running - every request would fail identically.")

    fields = list(schema.get("properties", {}).keys())
    print(f"  prompt: {len(prompt)} chars, schema: {len(fields)} fields ({', '.join(fields)})")
    columns = ["_id", "_status", "_chunk_count", "_char_count"] + fields

    # ── 2. Duplicates ────────────────────────────────────────────────────────
    confirm("scan the identifier list for duplicates", args.interactive)
    print("\n[2/3] Checking identifier list")
    ids, duplicates = read_id_list(args.files)
    if duplicates:
        print(f"  {len(ids)} unique identifiers, {duplicates} duplicate line(s) dropped")
        print(f"  (each duplicate would have been a redundant LLM call)")
    else:
        print(f"  {len(ids)} unique identifiers, no duplicates")

    # ── 3. KB match ──────────────────────────────────────────────────────────
    confirm(f"log in to {frontend} and check the KB contents", args.interactive)
    print("\n[3/3] Checking the knowledge base")
    password = args.password or os.getenv("LANCY_PASSWORD") or getpass.getpass("Lancy password: ")
    admin_escape = os.getenv("LANCY_ADMIN") == "1"

    session = requests.Session()
    login(session, frontend, password, admin_escape)

    cfg = fetch_config(session, frontend)
    if cfg:
        num_ctx = cfg.get("num_ctx", 0)
        budget = int(num_ctx * 0.6 * 3.5)  # mirrors _BUDGET_FRACTION in rag_router.py
        print(f"  backend={cfg.get('llm_backend')} model={cfg.get('llm_model')} num_ctx={num_ctx}")
        print(f"  per-document budget ~{budget:,} chars - larger documents return over_budget")
        if cfg.get("llm_backend") == "ollama":
            print("  note: ollama does not enforce the schema at decode time, it only guides via the prompt")

    if args.id_field == "source_file":
        ingested = fetch_ingested_files(session, frontend, args.kb)
        if ingested is None:
            print("  could not read store-info - skipping the identifier check")
        else:
            missing = [i for i in ids if i not in ingested]
            target = args.kb or "active KB"
            print(f"  {target}: {len(ingested)} distinct source_file values")
            print(f"  {len(ids) - len(missing)}/{len(ids)} identifiers matched, {len(missing)} missing")
            if missing:
                for m in missing[:10]:
                    print(f"    missing: {m}")
                if len(missing) > 10:
                    print(f"    ... and {len(missing) - 10} more")
                if len(missing) == len(ids):
                    print("  NOTHING matched - wrong --kb, wrong --id-field, or the KB is not ingested.")
                print("  Missing ids still get a row, with _status=no_chunks (no LLM call).")
    else:
        # store-info only reports source_file, so document_id lists cannot be checked here.
        print("  --id-field document_id: store-info only exposes source_file, cannot verify ids")

    if args.dry_run:
        print("\nDry run - stopping before analysis.")
        return

    processed = set() if args.force else load_processed(args.output)
    todo = [i for i in ids if i not in processed]
    print(f"\n{len(ids)} total, {len(processed)} already done, {len(todo)} to process.")
    if not todo:
        print("Nothing to do.")
        return
    confirm(f"analyze {len(todo)} document(s) and append to {args.output}", args.interactive)
    print()

    write_header = not args.output.exists() or args.force
    mode = "w" if args.force else "a"
    with args.output.open(mode, newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if write_header:
            writer.writeheader()
            fh.flush()

        for i, ident in enumerate(todo, 1):
            t0 = time.monotonic()
            row = analyze_one(
                session, args.frontend.rstrip("/"), args.id_field, ident, prompt, schema, args.kb,
            )
            row["_id"] = ident
            writer.writerow(row)
            fh.flush()
            elapsed = time.monotonic() - t0
            print(f"  [{i}/{len(todo)}] {ident} -> {row['_status']}  ({elapsed:.1f}s)")

    print(f"Done. Wrote {args.output}")


if __name__ == "__main__":
    main()
