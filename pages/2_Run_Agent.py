"""Run agent — send a submitted version's questions + the trial's SAP to an LLM
and see the reproduced statistical design.

Each user supplies their OWN API key (nothing is stored server-side), so this
page never spends the Space owner's credits.
"""

from __future__ import annotations

import json

import streamlit as st

from lib.agent import (
    PROVIDERS,
    build_prompt_block,
    build_user_message,
    call_model,
    efforts_for,
    extract_blocks,
    load_sap_text,
    model_info,
    models_for,
    provider_for,
)
from lib.storage import (
    get_submission,
    hf_configured,
    list_agent_runs,
    list_versions,
    save_agent_run,
)

st.set_page_config(page_title="TDB — Run agent", page_icon="🤖", layout="centered")

st.title("🤖 Run agent")
st.caption(
    "Send a submitted version's questions plus the trial's SAP to an LLM, and "
    "compare the model's reproduced design against the questions."
)

if not hf_configured:
    st.info("ℹ️ HF env vars not set — reading/writing `./data/` (local dev mode).")


# ------------- state ------------------------------------------------------

if "ra_versions" not in st.session_state:
    st.session_state.ra_versions = []
if "ra_result" not in st.session_state:
    st.session_state.ra_result = None


@st.cache_data(show_spinner=False)
def _sap(doc_id: str, kind: str) -> str:
    return load_sap_text(doc_id, kind=kind)


# ------------- 1. pick a submission --------------------------------------

st.subheader("1. Pick a submission")

c1, c2 = st.columns(2)
with c1:
    st.text_input("DOI", key="trial_id", placeholder="e.g., 10.1200_jco.22.01989")
with c2:
    st.text_input("Username", key="username", placeholder="e.g., jdoe")

doi = st.session_state.get("trial_id", "").strip().lower()
user = st.session_state.get("username", "").strip()


def _find() -> None:
    if not doi or not user:
        st.session_state.ra_versions = []
        st.session_state.ra_result = {"kind": "error", "msg": "Enter DOI and username first."}
        return
    try:
        st.session_state.ra_versions = list_versions(doi, user)
        st.session_state.ra_result = None
    except Exception as e:
        st.session_state.ra_versions = []
        st.session_state.ra_result = {"kind": "error", "msg": f"Lookup failed: {e}"}


st.button("Find versions", on_click=_find)

versions = st.session_state.ra_versions
selected = None
if versions:
    def _label(sid: str) -> str:
        v = next((x for x in versions if x["submissionId"] == sid), None)
        return f"{v['submittedAt']}  ·  {v['num_questions']} Q" if v else sid

    selected = st.selectbox(
        "Version to run",
        options=[v["submissionId"] for v in versions],
        format_func=_label,
        key="ra_version_select",
    )
elif doi and user:
    st.caption('Click "Find versions" to list this submission\'s versions.')

# ------------- 2. provider → model → effort → key -------------------------

st.subheader("2. Provider, model & effort")

provider = st.selectbox("Provider", options=PROVIDERS, key="ra_provider")

entries = models_for(provider)
model_ids = [e["id"] for e in entries] + ["Other (type an id)…"]
labels = {e["id"]: e["label"] for e in entries}
picked = st.selectbox(
    "Model",
    options=model_ids,
    format_func=lambda mid: labels.get(mid, mid),
    key="ra_model_pick",
)

model = picked
if picked == "Other (type an id)…":
    model = st.text_input(
        "Model id",
        key="ra_model_custom",
        placeholder="claude-opus-5" if provider == "Anthropic" else "gpt-5.6-sol",
    ).strip()

# Reasoning effort — only for models that accept it.
effort = ""
if model:
    levels = efforts_for(model)
    if levels:
        info = model_info(model)
        default = info.get("default_effort") or "high"
        idx = levels.index(default) if default in levels else 0
        effort = st.selectbox(
            "Reasoning effort",
            options=levels,
            index=idx,
            key="ra_effort",
            help=(
                "Anthropic: sent as output_config.effort. "
                "OpenAI: sent as reasoning_effort. Higher = deeper reasoning, "
                "more tokens, slower and more expensive."
            ),
        )
    else:
        st.caption(f"`{model}` does not support a reasoning-effort setting.")

# API key for the chosen provider.
prov_key = provider_for(model) if model else ""
api_key = ""
if model:
    if prov_key == "anthropic":
        api_key = st.text_input("Anthropic API key", type="password", placeholder="sk-ant-…")
    elif prov_key == "openai":
        api_key = st.text_input("OpenAI API key", type="password", placeholder="sk-…")
    else:
        st.warning(
            f"Can't tell the provider for `{model}` — use a claude-* or gpt-*/o*-* id."
        )
    if prov_key:
        st.caption("🔒 Your key is used only for this run — it is never stored or logged.")

doc_kind = st.radio(
    "Input document", ["sap", "protocol"], horizontal=True,
    help="Which document to feed the model.",
)

# ------------- 3. run -----------------------------------------------------

st.subheader("3. Run")

runnable = bool(selected and model and prov_key and api_key)
if st.button("▶️ Run agent", type="primary", disabled=not runnable):
    try:
        record = get_submission(selected)
        if not record:
            raise RuntimeError("Could not load that submission version.")
        block = build_prompt_block(record)
        n_q = len(block["prompt"])
        if n_q == 0:
            raise RuntimeError("That version has no questions.")

        with st.spinner(f"Loading {doc_kind} text…"):
            sap_text = _sap(doi, doc_kind)
        st.caption(f"{n_q} question(s) · {len(sap_text):,} chars of {doc_kind} text")

        user_msg = build_user_message(sap_text, block)
        version = record.get("version", "")
        eff_note = f" at effort={effort}" if effort else ""
        with st.spinner(f"Running {model}{eff_note}… (a large SAP can take 1–3 minutes)"):
            raw = call_model(model, user_msg, api_key=api_key, effort=effort or None)
        out_json, out_r, err = extract_blocks(raw)
        saved = save_agent_run(
            doi, user, model, version, out_json, out_r, raw, err,
            provider=provider, effort=effort,
        )
        st.session_state.ra_result = {
            "kind": "ok",
            "model": model,
            "effort": effort,
            "json": out_json,
            "r": out_r,
            "err": err,
            "raw": raw,
            "saved": saved,
        }
    except Exception as e:
        st.session_state.ra_result = {"kind": "error", "msg": str(e)}

if not runnable:
    st.caption("Pick a version, a model, and enter the matching API key.")

# ------------- results ----------------------------------------------------

res = st.session_state.ra_result
if res and res.get("kind") == "error":
    st.error(res["msg"])


def _render_answers(out_json: dict) -> None:
    items = (out_json or {}).get("output") or []
    if not items:
        st.caption("No answers in the model's output.json.")
        return
    for it in items:
        with st.container(border=True):
            st.markdown(
                f"**`{it.get('id','')}` · {it.get('design_element','—')} · "
                f"`{it.get('question_type','')}`**"
            )
            st.markdown(f"> {it.get('question','')}")
            out = it.get("output") or {}
            if "extracted_value" in out:
                st.markdown("**extracted_value**")
                st.write(out.get("extracted_value") or "—")
            dims = out.get("dimensions") or {}
            for k in ("inputs_used", "method", "calculated_value"):
                if k in dims:
                    st.markdown(f"**{k}**")
                    st.write(dims.get(k) or "—")


if res and res.get("kind") == "ok":
    st.divider()
    tag = f"{res['model']}" + (f" · effort={res['effort']}" if res.get("effort") else "")
    st.subheader(f"Results — {tag}")
    if res.get("err"):
        st.warning(f"Could not parse output.json ({res['err']}) — see raw response below.")
    else:
        saved = res.get("saved") or {}
        st.success(f"Saved to `{saved.get('path','')}`")
        if saved.get("url"):
            st.markdown(f"[View on Hugging Face]({saved['url']})")
    if res.get("json"):
        _render_answers(res["json"])
        st.download_button(
            "Download output.json",
            data=json.dumps(res["json"], indent=2, ensure_ascii=False),
            file_name=f"output__{res['model']}.json",
            mime="application/json",
        )
    if res.get("r"):
        st.markdown("**output.R**")
        st.code(res["r"], language="r")
        st.download_button(
            "Download output.R",
            data=res["r"],
            file_name=f"output__{res['model']}.R",
            mime="text/plain",
        )
    if st.checkbox("Show raw response"):
        st.code(res.get("raw", ""))

# ------------- past runs --------------------------------------------------

if doi and user:
    st.divider()
    if st.toggle("📜 Past agent runs for this submission", key="ra_show_past"):
        try:
            runs = list_agent_runs(doi, user)
        except Exception as e:
            runs = []
            st.error(f"Could not list past runs: {e}")
        if not runs:
            st.caption("No agent runs saved yet.")
        for run in runs:
            eff = run.get("effort")
            head = f"{run.get('ranAt','')} · {run.get('model','')}"
            if eff:
                head += f" · effort={eff}"
            with st.expander(head):
                st.caption(f"submission version: `{run.get('submission_version','')}`")
                if run.get("error"):
                    st.warning(f"parse error: {run['error']}")
                if run.get("output_json"):
                    _render_answers(run["output_json"])
                if run.get("output_r"):
                    st.code(run["output_r"], language="r")
