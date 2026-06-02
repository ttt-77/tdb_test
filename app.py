"""Clinical Trial AI Reproduction Benchmark — Intake form.

Run locally:
    streamlit run app.py

Deployed to Hugging Face Space; submissions are committed to an HF Dataset repo.
See README.md for setup.
"""

from __future__ import annotations

import json
from typing import List

import streamlit as st

from lib.schema import (
    DESIGN_ELEMENTS,
    QUESTION_TYPES,
    Question,
    blank_question,
    next_question_id,
    rubrics_for_type,
)
from lib.storage import create_submission, hf_configured

st.set_page_config(
    page_title="TDB Intake",
    page_icon="🔬",
    layout="centered",
)

# ------------- state init ------------------------------------------------

if "questions" not in st.session_state:
    st.session_state.questions = []  # type: List[Question]
if "trial_id" not in st.session_state:
    st.session_state.trial_id = ""
if "username" not in st.session_state:
    st.session_state.username = ""
if "last_result" not in st.session_state:
    st.session_state.last_result = None


# ------------- callbacks -------------------------------------------------

def _add_question() -> None:
    qid = next_question_id(st.session_state.questions)
    st.session_state.questions.append(blank_question(qid))


def _remove_question(idx: int) -> None:
    st.session_state.questions.pop(idx)


def _change_question_type(q_idx: int, new_type: str) -> None:
    """Regenerate rubrics when question_type changes."""
    q = st.session_state.questions[q_idx]
    if q["question_type"] != new_type:
        q["question_type"] = new_type
        q["rubrics"] = rubrics_for_type(new_type)


def _save_draft() -> None:
    """Stash form into session_state (already there); just signal."""
    st.session_state.last_result = {"kind": "draft", "msg": "Draft saved in this browser session."}


def _submit() -> None:
    trial_id = st.session_state.trial_id.strip()
    username = st.session_state.username.strip()
    if not trial_id or not username:
        st.session_state.last_result = {"kind": "error", "msg": "trial_id and username are required."}
        return

    comparison = {
        "trial_id": trial_id,
        "username": username,
        "prompts": st.session_state.questions,
    }
    try:
        result = create_submission(trial_id, username, comparison)
        st.session_state.last_result = {
            "kind": "success",
            "msg": f"Submitted: `{result['submissionId']}`",
            "url": result.get("url"),
        }
        # Reset form
        st.session_state.questions = []
        st.session_state.trial_id = ""
        st.session_state.username = ""
    except Exception as e:
        st.session_state.last_result = {"kind": "error", "msg": f"Submit failed: {e}"}


# ------------- header ----------------------------------------------------

st.title("Clinical Trial AI Reproduction Benchmark")
st.caption("Statistician intake form")

if not hf_configured:
    st.info(
        "ℹ️ HF env vars not set — submissions will be written to `./data/submissions/` "
        "(local dev mode)."
    )

# ------------- top fields ------------------------------------------------

c1, c2 = st.columns(2)
with c1:
    st.text_input("trial_id", key="trial_id", placeholder="e.g., NCT01234567")
with c2:
    st.text_input("username", key="username", placeholder="e.g., jdoe")

st.divider()

# ------------- questions list --------------------------------------------

st.subheader("Questions")

if not st.session_state.questions:
    st.caption('No questions yet. Click "Add question" below to begin.')

for i, q in enumerate(st.session_state.questions):
    with st.container(border=True):
        head_l, head_r = st.columns([6, 1])
        with head_l:
            new_id = st.text_input(
                "id",
                value=q["id"],
                key=f"q_{i}_id",
                label_visibility="collapsed",
            )
            q["id"] = new_id
        with head_r:
            st.button("Remove", key=f"rm_{i}", on_click=_remove_question, args=(i,))

        col1, col2 = st.columns(2)
        with col1:
            de_options = [""] + DESIGN_ELEMENTS
            de_idx = de_options.index(q["design_element"]) if q["design_element"] in de_options else 0
            new_de = st.selectbox(
                "design_element",
                options=de_options,
                index=de_idx,
                key=f"q_{i}_de",
                format_func=lambda x: "— select —" if x == "" else x,
            )
            q["design_element"] = new_de
            if new_de == "Others":
                q["design_element_other"] = st.text_input(
                    "Specify other design element",
                    value=q.get("design_element_other", ""),
                    key=f"q_{i}_de_other",
                )
            else:
                q["design_element_other"] = ""

        with col2:
            qt_options = [""] + QUESTION_TYPES
            qt_idx = qt_options.index(q["question_type"]) if q["question_type"] in qt_options else 0
            new_qt = st.selectbox(
                "question_type",
                options=qt_options,
                index=qt_idx,
                key=f"q_{i}_qt",
                format_func=lambda x: "— select —" if x == "" else x,
            )
            # If question_type changed, regenerate rubrics via callback-like pattern.
            if new_qt != q["question_type"]:
                _change_question_type(i, new_qt)

        new_question = st.text_input(
            "question",
            value=q["question"],
            key=f"q_{i}_question",
            placeholder="e.g., Alpha allocated to PFS",
        )
        q["question"] = new_question

        if q["rubrics"]:
            st.markdown(f"**Rubrics ({len(q['rubrics'])})**")
            for j, r in enumerate(q["rubrics"]):
                with st.container(border=True):
                    meta_parts = [f"**Artifact:** `{r['artifact']}`"]
                    if r["dimension"]:
                        meta_parts.append(f"**Dimension:** {r['dimension']}")
                    st.markdown(" · ".join(meta_parts))

                    rc1, rc2 = st.columns(2)
                    with rc1:
                        r["points"] = st.text_input(
                            "points",
                            value=r["points"],
                            key=f"q_{i}_r_{j}_points",
                        )
                    with rc2:
                        r["tolerance"] = st.text_input(
                            "tolerance",
                            value=r["tolerance"],
                            key=f"q_{i}_r_{j}_tolerance",
                        )
                    r["criterion"] = st.text_area(
                        "criterion",
                        value=r["criterion"],
                        key=f"q_{i}_r_{j}_criterion",
                        height=80,
                    )

st.button("+ Add question", on_click=_add_question)

st.divider()

# ------------- actions ---------------------------------------------------

action_l, action_r = st.columns([1, 1])
with action_l:
    st.button("Save draft", on_click=_save_draft, use_container_width=True)
with action_r:
    st.button("Submit", on_click=_submit, type="primary", use_container_width=True)

# ------------- status banner ---------------------------------------------

res = st.session_state.last_result
if res:
    if res["kind"] == "success":
        st.success(res["msg"])
        if res.get("url"):
            st.markdown(f"[View on Hugging Face]({res['url']})")
    elif res["kind"] == "error":
        st.error(res["msg"])
    else:
        st.info(res["msg"])

with st.expander("Debug: current form state (JSON)"):
    st.code(
        json.dumps(
            {
                "trial_id": st.session_state.trial_id,
                "username": st.session_state.username,
                "prompts": st.session_state.questions,
            },
            indent=2,
            ensure_ascii=False,
        ),
        language="json",
    )
