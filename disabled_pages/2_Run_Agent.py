"""Run agent — give an LLM a workspace containing the trial's SAP and let it
work: explore the document with tools, then write output.json and output.R.

Each user supplies their OWN API key (nothing is stored server-side), so this
page never spends the Space owner's credits.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import streamlit as st

from lib.agent import (
    PROVIDERS,
    build_prompt_block,
    efforts_for,
    load_sap_text,
    model_info,
    models_for,
    provider_for,
)
from lib.agent_loop import run_agent
from lib.storage import (
    get_submission,
    hf_configured,
    list_agent_runs,
    list_versions,
    save_agent_run,
)
from lib.tools import Workspace

st.set_page_config(page_title="TDB — Run agent", page_icon="🤖", layout="centered")

st.title("🤖 Run agent")
st.caption(
    "The agent gets a workspace containing the trial's SAP, explores it with "
    "tools (grep / read), then writes `output.json` and `output.R` — and runs the "
    "R script to verify it."
)

if not hf_configured:
    st.info("ℹ️ HF env vars not set — reading/writing `./data/` (local dev mode).")
if not shutil.which("Rscript"):
    st.warning(
        "⚠️ `Rscript` isn't installed here, so the agent can't execute output.R "
        "(it will be told so and continue). Add `r-base` to `apt.txt` to enable it."
    )


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

ac1, ac2 = st.columns(2)
with ac1:
    doc_kind = st.radio(
        "Document in the workspace", ["sap", "protocol"], horizontal=True,
        help="Written into the workspace as <kind>.txt with page markers.",
    )
with ac2:
    max_iters = st.number_input(
        "Max tool-use iterations", min_value=1, max_value=100, value=30, step=5,
        help="Safety cap on the agent loop.",
    )

# ------------- 3. run -----------------------------------------------------

st.subheader("3. Run")

runnable = bool(selected and model and prov_key and api_key)
if st.button("▶️ Run agent", type="primary", disabled=not runnable):
    tmpdir = None
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

        # Build the workspace the agent will work in.
        tmpdir = tempfile.mkdtemp(prefix="tdb_agent_")
        ws = Workspace(Path(tmpdir))
        ws.write_file(f"{doc_kind}.txt", sap_text)
        st.caption(
            f"{n_q} question(s) · workspace `{doc_kind}.txt` "
            f"({len(sap_text):,} chars, {sap_text.count(chr(10)) + 1:,} lines)"
        )

        log = st.status(f"Agent working with {model}…", expanded=True)

        def on_event(ev: dict) -> None:
            if ev["kind"] == "tool":
                args = {k: v for k, v in (ev.get("args") or {}).items() if k != "content"}
                if "content" in (ev.get("args") or {}):
                    args["content"] = f"<{len(ev['args']['content']):,} chars>"
                head = (ev.get("result") or "").splitlines()[:1]
                log.write(
                    f"🔧 **{ev['name']}** `{json.dumps(args, ensure_ascii=False)[:160]}`"
                    + (f" → {head[0][:160]}" if head else "")
                )
            else:
                log.write(f"💬 {ev.get('text','')[:400]}")

        result = run_agent(
            model=model,
            workspace=ws,
            prompt_block=block,
            api_key=api_key,
            effort=effort or None,
            max_iterations=int(max_iters),
            on_event=on_event,
        )
        log.update(
            label=f"Agent finished — {result['iterations']} iteration(s)", state="complete"
        )

        # Collect the files the agent produced.
        out_json, out_r, parse_err = None, "", None
        oj = Path(tmpdir) / "output.json"
        orr = Path(tmpdir) / "output.R"
        if oj.is_file():
            try:
                out_json = json.loads(oj.read_text(encoding="utf-8"))
            except Exception as e:
                parse_err = f"output.json is not valid JSON: {e}"
        else:
            parse_err = "the agent never wrote output.json"
        if orr.is_file():
            out_r = orr.read_text(encoding="utf-8")

        tool_names = [s["name"] for s in result["steps"] if s["kind"] == "tool"]
        saved = save_agent_run(
            doi, user, model, record.get("version", ""), out_json, out_r,
            raw=result.get("final_text", ""), error=parse_err,
            provider=provider, effort=effort,
            extra={
                "mode": "agent",
                "document": f"{doc_kind}.txt",
                "iterations": result["iterations"],
                "stopped_early": result["stopped_early"],
                "usage": result["usage"],
                "tool_calls": len(tool_names),
                "ran_r": "run_r" in tool_names,
                "steps": result["steps"],
            },
        )
        st.session_state.ra_result = {
            "kind": "ok", "model": model, "effort": effort,
            "json": out_json, "r": out_r, "err": parse_err,
            "result": result, "saved": saved,
        }
    except Exception as e:
        st.session_state.ra_result = {"kind": "error", "msg": f"{type(e).__name__}: {e}"}
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

if not runnable:
    st.caption("Pick a version, a model, and enter the matching API key.")

# ------------- results ----------------------------------------------------

res = st.session_state.ra_result
if res and res.get("kind") == "error":
    st.error(res["msg"])


def _render_answers(out_json: dict) -> None:
    items = (out_json or {}).get("output") or []
    if not items:
        st.caption("No answers in the agent's output.json.")
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

    r = res["result"]
    u = r.get("usage") or {}
    m1, m2, m3 = st.columns(3)
    m1.metric("Iterations", r.get("iterations", 0))
    m2.metric("Tool calls", sum(1 for s in r["steps"] if s["kind"] == "tool"))
    m3.metric("Tokens in/out", f"{u.get('input_tokens',0):,}/{u.get('output_tokens',0):,}")
    if r.get("stopped_early"):
        st.warning("Hit the iteration cap before the agent finished on its own.")

    if res.get("err"):
        st.warning(res["err"])
    else:
        saved = res.get("saved") or {}
        st.success(f"Saved to `{saved.get('path','')}`")
        if saved.get("url"):
            st.markdown(f"[View on Hugging Face]({saved['url']})")

    if r.get("final_text"):
        with st.container(border=True):
            st.markdown("**Agent summary**")
            st.markdown(r["final_text"])

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
            "Download output.R", data=res["r"],
            file_name=f"output__{res['model']}.R", mime="text/plain",
        )

    if st.checkbox("Show full transcript"):
        for s in r["steps"]:
            if s["kind"] == "tool":
                st.markdown(f"**[{s['iteration']}] 🔧 {s['name']}**")
                st.code(json.dumps(s.get("args") or {}, indent=2, ensure_ascii=False)[:1500])
                st.code((s.get("result") or "")[:3000])
            else:
                st.markdown(f"**[{s['iteration']}] 💬**")
                st.markdown(s.get("text", "")[:3000])

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
            head = f"{run.get('ranAt','')} · {run.get('model','')}"
            if run.get("effort"):
                head += f" · effort={run['effort']}"
            if run.get("iterations"):
                head += f" · {run['iterations']} iter"
            with st.expander(head):
                st.caption(
                    f"version `{run.get('submission_version','')}` · "
                    f"mode {run.get('mode','call')} · "
                    f"tool calls {run.get('tool_calls','—')} · "
                    f"ran R: {run.get('ran_r', False)}"
                )
                if run.get("error"):
                    st.warning(run["error"])
                if run.get("output_json"):
                    _render_answers(run["output_json"])
                if run.get("output_r"):
                    st.code(run["output_r"], language="r")
