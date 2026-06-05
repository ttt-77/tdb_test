"""Trial Design Benchmark — Intake form.

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
    question_content_hash,
    rubrics_for_type,
)
from lib.storage import (
    get_submission,
    hf_configured,
    list_versions,
    pair_reviews,
    save_submission,
)

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
# Bumped whenever we replace the questions wholesale (load / reset) so the
# per-question widgets get fresh keys and actually show the new values.
if "form_nonce" not in st.session_state:
    st.session_state.form_nonce = 0
# Versions found for the current trial_id + username (after "Find versions").
if "versions" not in st.session_state:
    st.session_state.versions = []
# All reviews across ALL versions of the current trial_id + username (history).
if "pair_reviews" not in st.session_state:
    st.session_state.pair_reviews = []
if "loaded_version" not in st.session_state:
    st.session_state.loaded_version = ""


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


def _find_versions() -> None:
    """Look up all saved versions for the current trial_id + username."""
    trial_id = st.session_state.trial_id.strip()
    username = st.session_state.username.strip()
    if not trial_id or not username:
        st.session_state.versions = []
        st.session_state.last_result = {
            "kind": "error",
            "msg": "Enter trial_id and username, then click Find versions.",
        }
        return
    try:
        versions = list_versions(trial_id, username)
        st.session_state.pair_reviews = pair_reviews(trial_id, username)
    except Exception as e:
        st.session_state.versions = []
        st.session_state.last_result = {"kind": "error", "msg": f"Lookup failed: {e}"}
        return
    st.session_state.versions = versions
    if not versions:
        st.session_state.last_result = {
            "kind": "info",
            "msg": f"No versions yet for `{trial_id}` / `{username}`. "
            "Add questions and Submit to create the first one.",
        }
    else:
        st.session_state.last_result = {
            "kind": "success",
            "msg": f"Found {len(versions)} version(s). Pick one below and click "
            "“Load selected version”.",
        }


def _load_selected() -> None:
    """Load the version chosen in the version selectbox into the form."""
    sub_id = st.session_state.get("version_select")
    if not sub_id:
        st.session_state.last_result = {"kind": "error", "msg": "Pick a version first."}
        return
    try:
        record = get_submission(sub_id)
    except Exception as e:
        st.session_state.last_result = {"kind": "error", "msg": f"Load failed: {e}"}
        return
    if not record:
        st.session_state.last_result = {"kind": "error", "msg": "That version could not be loaded."}
        return
    prompts = (record.get("comparison") or {}).get("prompts") or []
    st.session_state.questions = prompts
    st.session_state.form_nonce += 1  # force question widgets to refresh
    st.session_state.loaded_version = record.get("version", "")
    st.session_state.last_result = {
        "kind": "success",
        "msg": f"Loaded version {record.get('version', '')} "
        f"({len(prompts)} question(s)). Edit and Submit to save a new version.",
    }


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
        result = save_submission(trial_id, username, comparison)
        st.session_state.last_result = {
            "kind": "success",
            "msg": f"Saved as new version `{result['version']}`. "
            "Use “Find versions” to see all versions.",
            "url": result.get("url"),
        }
        # Refresh the version list so the new version shows up.
        try:
            st.session_state.versions = list_versions(trial_id, username)
        except Exception:
            pass
        # Keep the form populated so the user can continue editing.
    except Exception as e:
        st.session_state.last_result = {"kind": "error", "msg": f"Submit failed: {e}"}


# ------------- header ----------------------------------------------------

st.title("Trial Design Benchmark")
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

st.button(
    "Find versions",
    on_click=_find_versions,
    help="List all previously submitted versions for this trial_id + username.",
)

_STATUS_EMOJI = {"pending": "🟡", "reviewed": "🟢", "needs_fix": "🔴"}

versions = st.session_state.versions
if versions:
    options = [v["submissionId"] for v in versions]

    def _ver_label(sid: str) -> str:
        v = next((x for x in versions if x["submissionId"] == sid), None)
        if not v:
            return sid
        emoji = _STATUS_EMOJI.get(v.get("status", "pending"), "⚪")
        rc = v.get("review_count", 0)
        rtag = f"{rc} review(s)" if rc else "no reviews"
        return (
            f"{v['submittedAt']}  ·  {v['num_questions']} Q  ·  "
            f"{emoji} {v.get('status','pending')} ({rtag})"
        )

    vc1, vc2 = st.columns([3, 1])
    with vc1:
        st.selectbox(
            "Select a version to load",
            options=options,
            format_func=_ver_label,
            key="version_select",
        )
    with vc2:
        st.write("")
        st.write("")
        st.button("Load selected version", on_click=_load_selected, use_container_width=True)

def _render_review_lines(reviews: list) -> None:
    """Render a list of reviews as markdown bullets, newest first (with version)."""
    for rev in reversed(reviews):
        emoji = _STATUS_EMOJI.get(rev.get("status", ""), "⚪")
        ver = rev.get("version", "")
        vtag = f" · on `v{ver}`" if ver else ""
        line = (
            f"- {emoji} **{rev.get('status','')}** — "
            f"{rev.get('reviewer') or 'anon'} · _{rev.get('at','')}_{vtag}"
        )
        if rev.get("note"):
            line += f"  \n  Reviews: {rev['note']}"
        st.markdown(line)


# Show OVERALL reviewer feedback across ALL versions of this trial (per-question
# feedback is shown inside each question block below).
pair_review_list = st.session_state.pair_reviews
overall_history = [r for r in pair_review_list if not r.get("question_id")]
if overall_history:
    with st.container(border=True):
        st.markdown(f"**Overall reviewer feedback — all versions ({len(overall_history)})**")
        _render_review_lines(overall_history)

st.divider()

# ------------- questions list --------------------------------------------

st.subheader("Questions")

if not st.session_state.questions:
    st.caption('No questions yet. Click "Add question" below to begin.')

n = st.session_state.form_nonce  # widget-key namespace; changes on load/reset

for i, q in enumerate(st.session_state.questions):
    with st.container(border=True):
        head_l, head_r = st.columns([6, 1])
        with head_l:
            new_id = st.text_input(
                "id",
                value=q["id"],
                key=f"q_{n}_{i}_id",
                label_visibility="collapsed",
            )
            q["id"] = new_id
        with head_r:
            st.button("Remove", key=f"rm_{n}_{i}", on_click=_remove_question, args=(i,))

        col1, col2 = st.columns(2)
        with col1:
            de_options = [""] + DESIGN_ELEMENTS
            de_idx = de_options.index(q["design_element"]) if q["design_element"] in de_options else 0
            new_de = st.selectbox(
                "design_element",
                options=de_options,
                index=de_idx,
                key=f"q_{n}_{i}_de",
                format_func=lambda x: "— select —" if x == "" else x,
            )
            q["design_element"] = new_de
            if new_de == "Others":
                q["design_element_other"] = st.text_input(
                    "Specify other design element",
                    value=q.get("design_element_other", ""),
                    key=f"q_{n}_{i}_de_other",
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
                key=f"q_{n}_{i}_qt",
                format_func=lambda x: "— select —" if x == "" else x,
            )
            # If question_type changed, regenerate rubrics via callback-like pattern.
            if new_qt != q["question_type"]:
                _change_question_type(i, new_qt)

        new_question = st.text_input(
            "question",
            value=q["question"],
            key=f"q_{n}_{i}_question",
            placeholder="e.g., Alpha allocated to PFS",
        )
        q["question"] = new_question

        # Reviewer feedback for this question across ALL versions of the trial.
        q_reviews = [
            r for r in st.session_state.pair_reviews if r.get("question_id") == q["id"]
        ]
        if q_reviews:
            latest = q_reviews[-1]  # oldest-first list
            latest_status = latest.get("status", "")
            reviewed_hash = latest.get("question_hash")
            current_hash = question_content_hash(q)
            # If the question was edited since the latest review, that review no
            # longer applies — flag it instead of showing the old status.
            stale = reviewed_hash is not None and reviewed_hash != current_hash
            if stale:
                st.warning(
                    f"✏️ Modified since last review (was “{latest_status}”) — "
                    "resubmit for re-review."
                )
            elif latest_status == "reviewed":
                st.success("✅ Pass — this question has been reviewed")
            elif latest_status == "needs_fix":
                st.error("🔴 Needs fix")
            elif latest_status == "pending":
                st.warning("🟡 Pending review")
            with st.container(border=True):
                st.markdown(
                    f"**Reviewer feedback on this question — all versions ({len(q_reviews)})**"
                )
                _render_review_lines(q_reviews)

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
                            key=f"q_{n}_{i}_r_{j}_points",
                        )
                    with rc2:
                        r["tolerance"] = st.text_input(
                            "tolerance",
                            value=r["tolerance"],
                            key=f"q_{n}_{i}_r_{j}_tolerance",
                        )
                    r["criterion"] = st.text_area(
                        "criterion",
                        value=r["criterion"],
                        key=f"q_{n}_{i}_r_{j}_criterion",
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
