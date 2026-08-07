#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "huggingface_hub>=0.25",
#     "pandas>=2.0",
#     "pyarrow>=15",
#     "anthropic>=0.40",
#     "openai>=1.40",
# ]
# ///
"""Populate a Trial Design Benchmark submission's answers by running LLMs.

For one intake submission it:
  1. loads the submission's latest version (questions + rubrics) from the
     private HF dataset `trialdesignbench/intake_form_data`,
  2. resolves the trial's parsed SAP from the public HF dataset
     `trialdesignbench/source` (documents/<doi>/sap.lines.json),
  3. asks each configured model to reproduce the statistical design, returning
     a filled output.json (+ output.R for derivation questions),
  4. writes the results to local files under --out.

Usage:
    export HF_TOKEN=hf_...            # needed for the private submissions repo
    export ANTHROPIC_API_KEY=...      # for Claude models
    export OPENAI_API_KEY=...         # for OpenAI models
    python run_llm.py --submission NCT02578680__EricZ
    python run_llm.py --submission NCT02578680__EricZ --doc-id 10.1056_nejmoa1801005 \
        --models claude-opus-5 gpt-5.6-sol

Outputs:
    out/<submission>/<model>/output.json   # completed prompt block
    out/<submission>/<model>/output.R      # R for derivation questions
    out/<submission>/<model>/raw.txt       # raw model response
    out/<submission>/prompt_block.json     # what the models were asked to fill
    out/<submission>/sap.txt               # SAP text fed to the models
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# The prompt and model-calling logic live in lib/agent.py so this script and the
# Streamlit "Run agent" page always use exactly the same prompt.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.agent import (  # noqa: E402
    RESPONSE_FORMAT_INSTRUCTION,  # noqa: F401  (re-exported for reference)
    SYSTEM_PROMPT,  # noqa: F401
    build_prompt_block,
    build_user_message,
    call_model,
    extract_blocks,
    sap_text_from_lines,
)

INTAKE_REPO = "trialdesignbench/intake_form_data"
SOURCE_REPO = "trialdesignbench/source"

DEFAULT_MODELS = ["claude-opus-5", "gpt-5.6-sol"]


# ---------------------------------------------------------------------------
# HF dataset access
# ---------------------------------------------------------------------------

def _hf():
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN", "").strip() or None
    return HfApi(token=token), token


def load_submission(submission: str, version: str | None = None) -> dict:
    """Load a submission version from HF.

    If `version` is given, use that exact file; otherwise use the latest
    (max timestamp) under submissions/<submission>/.
    """
    api, token = _hf()
    from huggingface_hub import hf_hub_download

    # Pin a specific version file if requested.
    if version:
        # Accept a bare basename, a full repo path, or a URL-encoded '+'.
        vname = version.rsplit("/", 1)[-1].replace("%2B", "+")
        if not vname.endswith(".json"):
            vname += ".json"
        target = f"submissions/{submission}/{vname}"
        try:
            path = hf_hub_download(
                repo_id=INTAKE_REPO, repo_type="dataset", filename=target, token=token
            )
        except Exception as e:
            sys.exit(f"Could not download pinned version {target} from {INTAKE_REPO}: {e}")
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        print(f"  using pinned version: {vname}")
        return rec

    prefix = f"submissions/{submission}/"
    try:
        files = api.list_repo_files(repo_id=INTAKE_REPO, repo_type="dataset")
    except Exception as e:
        sys.exit(
            f"Could not list {INTAKE_REPO} (private?). Set HF_TOKEN with read "
            f"access. Error: {e}"
        )
    versions = [f for f in files if f.startswith(prefix) and f.endswith(".json")]
    if not versions:
        # Fall back: maybe the submission is a single flat file.
        flat = [f for f in files if f == f"submissions/{submission}.json"]
        if not flat:
            sys.exit(f"No submission files found under {prefix} in {INTAKE_REPO}.")
        versions = flat
    # Version filenames are ISO timestamps (zero-padded, fixed format), so the
    # max basename is the most recent submission.
    versions.sort(key=lambda f: f.rsplit("/", 1)[-1])
    latest = versions[-1]
    path = hf_hub_download(
        repo_id=INTAKE_REPO, repo_type="dataset", filename=latest, token=token
    )
    with open(path, encoding="utf-8") as fh:
        rec = json.load(fh)
    print(f"  found {len(versions)} version(s); using latest: {latest.rsplit('/', 1)[-1]}")
    return rec


def load_submission_from_file(path: str) -> dict:
    """Load a submission JSON from a local file."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"--submission-file not found: {path}")
    rec = json.loads(p.read_text(encoding="utf-8"))
    # Accept either a full submission record or a bare {trial_id, username, prompts}.
    if "comparison" not in rec and "prompts" in rec:
        rec = {"comparison": rec}
    print(f"  submission file: {path}")
    return rec


def resolve_doc_id(nct_id: str, override: str | None) -> str:
    """Map an NCT id to a documents/<doi> folder via tdr.parquet, or use override."""
    api, token = _hf()
    if override:
        return override
    from huggingface_hub import hf_hub_download

    parquet = hf_hub_download(
        repo_id=SOURCE_REPO, repo_type="dataset", filename="data/tdr.parquet", token=token
    )
    import pandas as pd

    df = pd.read_parquet(parquet)
    rows = df[df["NCT ID"].astype(str).str.strip() == nct_id]
    if rows.empty:
        sys.exit(f"NCT {nct_id} not found in tdr.parquet; pass --doc-id explicitly.")

    existing = set(_list_doc_folders(api))
    candidates = []
    for link in rows["Paper Link"].dropna().astype(str):
        m = re.search(r"(10\.\d{4,9}/\S+)", link)
        if not m:
            continue
        folder = m.group(1).replace("/", "_").rstrip(".")
        candidates.append(folder)
    for folder in candidates:
        if folder in existing:
            print(f"  resolved {nct_id} -> documents/{folder}")
            return folder
    sys.exit(
        f"None of the DOI folders for {nct_id} exist in {SOURCE_REPO}: {candidates}. "
        f"Pass --doc-id explicitly."
    )


def _list_doc_folders(api) -> list[str]:
    files = api.list_repo_files(repo_id=SOURCE_REPO, repo_type="dataset")
    return sorted({f.split("/")[1] for f in files if f.startswith("documents/") and "/" in f[len("documents/") :]})


def load_sap_from_file(path: str) -> str:
    """Load SAP from a local file: .json -> reconstruct with page markers,
    anything else -> read as plain text."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"--sap-file not found: {path}")
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        text = sap_text_from_lines(data)
    else:
        text = p.read_text(encoding="utf-8").strip()
    if not text:
        sys.exit(f"SAP file {path} produced empty text.")
    return text


def load_sap_text(doc_id: str) -> str:
    """Reconstruct SAP text with page markers from documents/<doc>/sap.lines.json (HF)."""
    api, token = _hf()
    from huggingface_hub import hf_hub_download

    fname = f"documents/{doc_id}/sap.lines.json"
    try:
        path = hf_hub_download(
            repo_id=SOURCE_REPO, repo_type="dataset", filename=fname, token=token
        )
    except Exception as e:
        sys.exit(f"Could not download {fname} from {SOURCE_REPO}: {e}")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    text = sap_text_from_lines(data)
    if not text:
        sys.exit(f"SAP text for {doc_id} was empty.")
    return text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submission", default="NCT02578680__EricZ",
                    help="submission folder name <trial>__<user> (read from HF)")
    ap.add_argument("--submission-file", default=None,
                    help="local submission JSON path (skips HF; no HF_TOKEN needed)")
    ap.add_argument("--version", default=None,
                    help="pin an exact version file under submissions/<submission>/ "
                         "(e.g. 2026-06-07T17-23-05-870000+00-00.json); default: latest")
    ap.add_argument("--doc-id", default=None,
                    help="documents/<doc-id> folder (default: resolve from NCT via tdr.parquet)")
    ap.add_argument("--sap-file", default=None,
                    help="local SAP file (.json sap.lines -> page markers, else plain text; skips HF)")
    ap.add_argument("--effort", default=None,
                    help="reasoning effort: low|medium|high|xhigh|max (Anthropic) or "
                         "none|low|medium|high|xhigh|max (OpenAI); omit for the model default")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                    help=f"model ids to run (default: {DEFAULT_MODELS})")
    ap.add_argument("--out", default="out", help="output directory")
    args = ap.parse_args()

    nct_id = args.submission.split("__")[0]
    out_dir = Path(args.out) / args.submission
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Submission: {args.submission}  (NCT {nct_id})")
    # --- submission: local file or HF ---
    if args.submission_file:
        submission = load_submission_from_file(args.submission_file)
    else:
        submission = load_submission(args.submission, version=args.version)
    # --- SAP: local file or HF ---
    if args.sap_file:
        sap_text = load_sap_from_file(args.sap_file)
    else:
        doc_id = resolve_doc_id(nct_id, args.doc_id)
        sap_text = load_sap_text(doc_id)
    print(f"  SAP chars: {len(sap_text):,}")

    prompt_block = build_prompt_block(submission)
    n_q = len(prompt_block["prompt"])
    print(f"  questions: {n_q}")
    (out_dir / "prompt_block.json").write_text(
        json.dumps(prompt_block, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "sap.txt").write_text(sap_text, encoding="utf-8")

    if n_q == 0:
        sys.exit("Submission has no questions; nothing to run.")

    user_msg = build_user_message(sap_text, prompt_block)

    for model in args.models:
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", model)
        mdir = out_dir / safe
        mdir.mkdir(parents=True, exist_ok=True)
        print(f"\n>>> {model}")
        try:
            raw = call_model(model, user_msg, effort=args.effort)
        except Exception as e:
            print(f"    FAILED: {e}")
            (mdir / "error.txt").write_text(str(e), encoding="utf-8")
            continue
        (mdir / "raw.txt").write_text(raw, encoding="utf-8")
        output_json, output_r, err = extract_blocks(raw)
        if output_json is not None:
            (mdir / "output.json").write_text(
                json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"    wrote output.json ({len((output_json.get('output') or []))} answers)")
        else:
            print(f"    could not parse output.json: {err} (see raw.txt)")
        (mdir / "output.R").write_text(output_r or "", encoding="utf-8")
        print(f"    wrote output.R ({len(output_r or '')} chars)")

    print(f"\nDone. Results in {out_dir}/")


if __name__ == "__main__":
    main()
