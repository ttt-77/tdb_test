"""Shared agent logic: the task prompt, SAP loading, and model calls.

Single source of truth for both the Streamlit "Run agent" page and the
standalone ``llm/run_llm.py`` script — keep it free of Streamlit imports.

API keys are passed explicitly (the app lets each user bring their own key);
if omitted, the SDKs fall back to ANTHROPIC_API_KEY / OPENAI_API_KEY.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

SOURCE_REPO = "trialdesignbench/source"

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
    '(an object with a single top-level key "output").\n'
    "2. A ```r block containing output.R. If there are no Derivation required "
    "questions, return an empty ```r block.\n"
    "Do not include any prose outside the two code blocks."
)

# ---------------------------------------------------------------------------
# Model registry (verified against the providers' docs, Aug 2026)
#
# Anthropic passes effort as output_config={"effort": ...} with levels
# low/medium/high/xhigh/max (default high); Claude Haiku 4.5 does NOT support it.
# OpenAI passes reasoning_effort=... with none/low/medium/high/xhigh/max.
# ---------------------------------------------------------------------------

PROVIDERS: List[str] = ["Anthropic", "OpenAI"]

_ANTHROPIC_ALL = ["low", "medium", "high", "xhigh", "max"]
_ANTHROPIC_NO_XHIGH = ["low", "medium", "high", "max"]
_OPENAI_ALL = ["none", "low", "medium", "high", "xhigh", "max"]

MODELS: Dict[str, List[Dict[str, Any]]] = {
    "Anthropic": [
        {
            "id": "claude-opus-5",
            "label": "Claude Opus 5 — recommended · 1M ctx · $5/$25",
            "efforts": _ANTHROPIC_ALL,
            "default_effort": "high",
        },
        {
            "id": "claude-fable-5",
            "label": "Claude Fable 5 — most capable · 1M ctx · $10/$50",
            "efforts": _ANTHROPIC_ALL,
            "default_effort": "high",
        },
        {
            "id": "claude-sonnet-5",
            "label": "Claude Sonnet 5 — fast + smart · 1M ctx · $3/$15",
            "efforts": _ANTHROPIC_ALL,
            "default_effort": "high",
        },
        {
            "id": "claude-haiku-4-5-20251001",
            "label": "Claude Haiku 4.5 — fastest · 200K ctx · $1/$5 (no effort)",
            "efforts": [],
            "default_effort": "",
        },
        {
            "id": "claude-opus-4-8",
            "label": "Claude Opus 4.8 (legacy) · 1M ctx · $5/$25",
            "efforts": _ANTHROPIC_ALL,
            "default_effort": "high",
        },
        {
            "id": "claude-sonnet-4-6",
            "label": "Claude Sonnet 4.6 (legacy) · 1M ctx · $3/$15",
            "efforts": _ANTHROPIC_NO_XHIGH,
            "default_effort": "medium",
        },
    ],
    "OpenAI": [
        {
            "id": "gpt-5.6-sol",
            "label": "GPT-5.6 Sol — flagship",
            "efforts": _OPENAI_ALL,
            "default_effort": "medium",
        },
        {
            "id": "gpt-5.6-terra",
            "label": "GPT-5.6 Terra — balanced",
            "efforts": _OPENAI_ALL,
            "default_effort": "medium",
        },
        {
            "id": "gpt-5.6-luna",
            "label": "GPT-5.6 Luna — most cost-efficient",
            "efforts": _OPENAI_ALL,
            "default_effort": "medium",
        },
    ],
}


def models_for(provider: str) -> List[Dict[str, Any]]:
    return MODELS.get(provider, [])


def model_info(model_id: str) -> Dict[str, Any]:
    """Registry entry for a model id ({} if it's a custom/unknown id)."""
    for entries in MODELS.values():
        for e in entries:
            if e["id"] == model_id:
                return e
    return {}


def efforts_for(model_id: str) -> List[str]:
    """Effort levels a model accepts. Unknown ids fall back to the provider's set."""
    info = model_info(model_id)
    if info:
        return list(info.get("efforts") or [])
    p = provider_for(model_id)
    if p == "anthropic":
        return list(_ANTHROPIC_ALL)
    if p == "openai":
        return list(_OPENAI_ALL)
    return []


# ---------------------------------------------------------------------------
# SAP loading (public source dataset)
# ---------------------------------------------------------------------------

def sap_text_from_lines(data: dict) -> str:
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


def load_sap_text(doc_id: str, kind: str = "sap", token: Optional[str] = None) -> str:
    """SAP/protocol text (with page markers) from documents/<doc>/<kind>.lines.json.

    Raises RuntimeError if the document can't be fetched or is empty.
    """
    from huggingface_hub import hf_hub_download

    fname = f"documents/{doc_id}/{kind}.lines.json"
    try:
        path = hf_hub_download(
            repo_id=SOURCE_REPO, repo_type="dataset", filename=fname, token=token
        )
    except Exception as e:
        raise RuntimeError(f"Could not download {fname} from {SOURCE_REPO}: {e}") from e
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    text = sap_text_from_lines(data)
    if not text:
        raise RuntimeError(f"{kind} text for {doc_id} was empty.")
    return text


# ---------------------------------------------------------------------------
# Prompt block (questions with null placeholders)
# ---------------------------------------------------------------------------

def build_prompt_block(submission: dict) -> dict:
    """Turn a submission's questions into the null-placeholder prompt block."""
    prompts = (submission.get("comparison") or {}).get("prompts") or []
    block = []
    for q in prompts:
        de = q.get("design_element", "")
        if de == "Others" and q.get("design_element_other"):
            de = q["design_element_other"]
        qtype = q.get("question_type", "")
        if qtype == "derivation_required":
            output: Dict[str, Any] = {
                "dimensions": {"inputs_used": None, "method": None, "calculated_value": None}
            }
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
# Model calls
# ---------------------------------------------------------------------------

def provider_for(model: str) -> str:
    m = (model or "").strip().lower()
    if m.startswith(("claude", "anthropic")):
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "openai")):
        return "openai"
    return ""


def call_anthropic(
    model: str,
    user_msg: str,
    api_key: Optional[str] = None,
    effort: Optional[str] = None,
) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key) if api_key else Anthropic()
    kwargs: Dict[str, Any] = {}
    if effort:
        # Anthropic takes effort inside output_config (not a top-level param).
        kwargs["output_config"] = {"effort": effort}
    # Thinking tokens count against max_tokens, so give high-effort runs room.
    max_tokens = 64000 if effort in ("xhigh", "max") else 16000
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                # Cache the large SAP-bearing block so re-runs are cheaper.
                "content": [
                    {
                        "type": "text",
                        "text": user_msg,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
        **kwargs,
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def call_openai(
    model: str,
    user_msg: str,
    api_key: Optional[str] = None,
    effort: Optional[str] = None,
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    kwargs: Dict[str, Any] = {}
    if effort:
        kwargs["reasoning_effort"] = effort
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        **kwargs,
    )
    return resp.choices[0].message.content or ""


def call_model(
    model: str,
    user_msg: str,
    api_key: Optional[str] = None,
    effort: Optional[str] = None,
) -> str:
    provider = provider_for(model)
    if provider == "anthropic":
        return call_anthropic(model, user_msg, api_key, effort)
    if provider == "openai":
        return call_openai(model, user_msg, api_key, effort)
    raise ValueError(
        f"Unknown model '{model}' — use a claude-* (Anthropic) or gpt-*/o*-* (OpenAI) id."
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def extract_blocks(text: str) -> Tuple[Optional[dict], str, Optional[str]]:
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
