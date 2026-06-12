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
        --models claude-opus-4-8 gpt-4o

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

# ---------------------------------------------------------------------------
# The task prompt (verbatim). Used as the system prompt.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = "\n".join(
    [
        "You are an experienced trial statistician. You will be provided with the Statistical Analysis Plan (SAP) or protocol from a Phase 3 registrational trial. Your task is to reproduce the statistical design by answering the evaluation questions below.",
        "",
        "There are two types of evaluation questions:",
        "",
        "- Extraction only: locate and report the parameter value directly from the SAP/protocol.",
        "- Derivation required: identify the source inputs from the SAP/protocol, calculate the requested parameter, explain the calculation method, and provide reproducible R code in output.R that implements the calculation and prints the final result.",
        "",
        "Closed-book constraint: use only the input document provided below. Do not draw on prior knowledge of this trial from any external source, including published papers, press releases, registry entries, amendments, or training data. If a value is absent or not derivable from the input document, state this explicitly.",
        "",
        "Every reported value must be traceable to a specific section and page of the input document, or to a calculation whose inputs are themselves traceable to a specific section and page.",
        "",
        "Every numeric value reported must be expressed to at least 4 decimal places unless otherwise specified.",
        "",
        "Do not assume any specific statistical method unless it is explicitly stated or directly derivable from the input document. If multiple methods are plausible, state the assumption made and justify it based on the input document.",
        "",
        "Output instructions:",
        "- Return a file named output.json containing a single block named 'output'. Copy the entire prompt block into 'output' and replace each null value with the extracted or derived result. Do not add, remove, rename, or modify any other fields.",
        "- Return a separate file named output.R implementing the calculations for all Derivation required questions. For each question the script must print: (1) the source inputs, (2) the calculation method and formula applied, and (3) the final calculated value.",
    ]
)

# How the harness asks the model to package its two files in one response.
RESPONSE_FORMAT_INSTRUCTION = (
    "Return your answer as exactly two fenced code blocks, in this order:\n"
    "1. A ```json block containing the completed output.json "
    "(an object with a single top-level key \"output\").\n"
    "2. A ```r block containing output.R. If there are no Derivation required "
    "questions, return an empty ```r block.\n"
    "Do not include any prose outside the two code blocks."
)

INTAKE_REPO = "trialdesignbench/intake_form_data"
SOURCE_REPO = "trialdesignbench/source"

DEFAULT_MODELS = ["claude-opus-4-8", "gpt-4o"]


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


def _sap_text_from_lines(data: dict) -> str:
    """Reconstruct SAP text with page markers from a parsed sap.lines.json dict."""
    chunks = []
    for page in data.get("pages", []):
        pageno = page.get("page", "?")
        chunks.append(f"\n===== Page {pageno} =====")
        for line in page.get("lines", []):
            txt = (line.get("text") or "").strip()
            if txt:
                chunks.append(txt)
    return "\n".join(chunks).strip()


def load_sap_from_file(path: str) -> str:
    """Load SAP from a local file: .json -> reconstruct with page markers,
    anything else -> read as plain text."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"--sap-file not found: {path}")
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        text = _sap_text_from_lines(data)
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
    text = _sap_text_from_lines(data)
    if not text:
        sys.exit(f"SAP text for {doc_id} was empty.")
    return text


# ---------------------------------------------------------------------------
# Build the prompt block (questions with null placeholders)
# ---------------------------------------------------------------------------

def build_prompt_block(submission: dict) -> dict:
    prompts = (submission.get("comparison") or {}).get("prompts") or []
    block = []
    for q in prompts:
        de = q.get("design_element", "")
        if de == "Others" and q.get("design_element_other"):
            de = q["design_element_other"]
        qtype = q.get("question_type", "")
        if qtype == "derivation_required":
            output = {"dimensions": {"inputs_used": None, "method": None, "calculated_value": None}}
        else:  # extraction_only (default)
            output = {"extracted_value": None}
        block.append(
            {
                "id": q.get("id", ""),
                "design_element": de,
                "question": q.get("question", ""),
                "question_type": qtype,
                "output": output,
            }
        )
    return {"prompt": block}


def build_user_message(sap_text: str, prompt_block: dict) -> str:
    return (
        "INPUT DOCUMENT (SAP / protocol):\n"
        "<<<BEGIN DOCUMENT>>>\n"
        f"{sap_text}\n"
        "<<<END DOCUMENT>>>\n\n"
        "PROMPT BLOCK — copy this whole block into 'output' and replace each null:\n"
        "```json\n"
        f"{json.dumps(prompt_block, indent=2, ensure_ascii=False)}\n"
        "```\n\n"
        f"{RESPONSE_FORMAT_INSTRUCTION}"
    )


# ---------------------------------------------------------------------------
# Model callers — return raw response text
# ---------------------------------------------------------------------------

def call_anthropic(model: str, sap_text: str, user_msg: str) -> str:
    from anthropic import Anthropic

    client = Anthropic()  # reads ANTHROPIC_API_KEY
    # Cache the large SAP-bearing user block so re-runs are cheaper.
    resp = client.messages.create(
        model=model,
        max_tokens=8192,
        system=[{"type": "text", "text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": user_msg,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def call_openai(model: str, sap_text: str, user_msg: str) -> str:
    from openai import OpenAI

    client = OpenAI()  # reads OPENAI_API_KEY
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    return resp.choices[0].message.content or ""


def call_model(model: str, sap_text: str, user_msg: str) -> str:
    if model.startswith(("claude", "anthropic")):
        return call_anthropic(model, sap_text, user_msg)
    if model.startswith(("gpt", "o1", "o3", "openai")):
        return call_openai(model, sap_text, user_msg)
    raise ValueError(f"Unknown model '{model}' — prefix with claude-/gpt-/o1-...")


# ---------------------------------------------------------------------------
# Parse the two fenced blocks out of a response
# ---------------------------------------------------------------------------

def extract_blocks(text: str) -> tuple[dict | None, str, str | None]:
    """Return (parsed_output_json, output_r, json_parse_error)."""
    json_match = re.search(r"```json\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    r_match = re.search(r"```r\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    output_json, err = None, None
    if json_match:
        raw = json_match.group(1).strip()
        try:
            output_json = json.loads(raw)
        except Exception as e:
            err = f"{e}"
    else:
        err = "no ```json block found"
    output_r = r_match.group(1).strip() if r_match else ""
    return output_json, output_r, err


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
            raw = call_model(model, sap_text, user_msg)
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
