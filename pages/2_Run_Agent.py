"""Run agent — send a submitted version's questions + the trial's SAP to an LLM
and see the reproduced statistical design.

Each user supplies their OWN API key (nothing is stored server-side), so this
page never spends the Space owner's credits.
"""

from __future__ import annotations

import json

import streamlit as st

from lib.agent import (
    SUGGESTED_MODELS,
    build_prompt_block,
    build_user_message,
    call_model,
    extract_blocks,
    load_sap_text,
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

# ------------- 2. models + API key ---------------------------------------

st.subheader("2. Models & API key")

models = st.multiselect(
    "Models to run",
    options=SUGGESTED_MODELS,
    default=[],
    help="Any claude-* goes to Anthropic; any gpt-*/o*-* goes to OpenAI.",
)
custom = st.text_input(
    "Other model ids (comma-separated)",
    placeholder="e.g., claude-opus-4-8, gpt-5.5",
)
models = list(dict.fromkeys(models + [m.strip() for m in custom.split(",") if m.strip()]))

needs_anthropic = any(provider_for(m) == "anthropic" for m in models)
needs_openai = any(provider_for(m) == "openai" for m in models)

anthropic_key = openai_key = ""
if needs_anthropic:
    anthropic_key = st.text_input("Anthropic API key", type="password", placeholder="sk-ant-…")
if needs_openai:
    openai_key = st.text_input("OpenAI API key", type="password", placeholder="sk-…")
if models:
    st.caption("🔒 Your key is used only for this run — it is never stored or logged.")

unknown = [m for m in models if not provider_for(m)]
if unknown:
    st.warning(f"Unknown provider for: {', '.join(unknown)} — use a claude-* or gpt-*/o*-* id.")

doc_kind = st.radio(
    "Input document", ["sap", "protocol"], horizontal=True,
    help="Which document to feed the model.",
)

# ------------- 3. run -----------------------------------------------------

st.subheader("3. Run")

runnable = bool(selected and models and not unknown)
if st.button("▶️ Run agent", type="primary", disabled=not runnable):
    try:
        record = get_submission(selected)
        if not record:
            raise RuntimeError("Could not load that submission version.")
        block = build_prompt_block(record)
        n_q = len(block["prompt"])
        if n_q == 0:
            raise RuntimeError("That version has no questions.")

        with st.spinner(f"Loading {doc_kind}.pdf text…"):
            sap_text = _sap(doi, doc_kind)
        st.caption(f"{n_q} question(s) · {len(sap_text):,} chars of {doc_kind} text")

        user_msg = build_user_message(sap_text, block)
        version = record.get("version", "")
        results = []
        for m in models:
            key = anthropic_key if provider_for(m) == "anthropic" else openai_key
            with st.spinner(f"Running {m}… (a large SAP can take 1–3 minutes)"):
                try:
                    raw = call_model(m, user_msg, api_key=key or None)
                    out_json, out_r, err = extract_blocks(raw)
                    saved = save_agent_run(
                        doi, user, m, version, out_json, out_r, raw, err
                    )
                    results.append(
                        {"model": m, "json": out_json, "r": out_r, "err": err,
                         "raw": raw, "saved": saved}
                    )
                except Exception as e:
                    results.append({"model": m, "error": str(e)})
        st.session_state.ra_result = {"kind": "ok", "results": results}
    except Exception as e:
        st.session_state.ra_result = {"kind": "error", "msg": str(e)}

if not runnable:
    st.caption("Pick a version, choose at least one model, and enter the matching API key.")

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
    st.subheader("Results")
    for r in res["results"]:
        st.markdown(f"### {r['model']}")
        if r.get("error"):
            st.error(f"Failed: {r['error']}")
            continue
        if r.get("err"):
            st.warning(f"Could not parse output.json ({r['err']}) — see raw response below.")
        else:
            saved = r.get("saved") or {}
            st.success(f"Saved to `{saved.get('path','')}`")
            if saved.get("url"):
                st.markdown(f"[View on Hugging Face]({saved['url']})")
        if r.get("json"):
            _render_answers(r["json"])
            st.download_button(
                "Download output.json",
                data=json.dumps(r["json"], indent=2, ensure_ascii=False),
                file_name=f"output__{r['model']}.json",
                mime="application/json",
                key=f"dl_json_{r['model']}",
            )
        if r.get("r"):
            st.markdown("**output.R**")
            st.code(r["r"], language="r")
            st.download_button(
                "Download output.R",
                data=r["r"],
                file_name=f"output__{r['model']}.R",
                mime="text/plain",
                key=f"dl_r_{r['model']}",
            )
        if st.checkbox("Show raw response", key=f"raw_{r['model']}"):
            st.code(r.get("raw", ""))

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
            with st.expander(f"{run.get('ranAt','')} · {run.get('model','')}"):
                st.caption(f"submission version: `{run.get('submission_version','')}`")
                if run.get("error"):
                    st.warning(f"parse error: {run['error']}")
                if run.get("output_json"):
                    _render_answers(run["output_json"])
                if run.get("output_r"):
                    st.code(run["output_r"], language="r")
