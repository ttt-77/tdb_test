"""Trial Design Benchmark — Intake form.

Run locally:
    streamlit run app.py

Deployed to Hugging Face Space; submissions are committed to an HF Dataset repo.
See README.md for setup.

State model
-----------
`st.session_state.questions` holds only the STRUCTURE of each question:
``{"_uid": int, "rubrics": [{"artifact", "dimension"}, ...]}``. All editable
*values* (id, design_element, question, points, ...) live in session_state under
widget keys derived from the question's stable uid. session_state is the single
source of truth — widgets use `key=` only (no `value=`/`index=`), which avoids
the one-rerun-lag ("type twice") bug that comes from mixing value= with key=.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from lib.schema import DESIGN_ELEMENTS, QUESTION_TYPES, rubrics_for_type
from lib.storage import (
    get_submission,
    hf_configured,
    list_versions,
    pair_reviews,
    save_submission,
)

st.set_page_config(page_title="TDB Intake", page_icon="🔬", layout="wide")

SOURCE_REPO = "trialdesignbench/source"

_STATUS_EMOJI = {"pending": "🟡", "reviewed": "🟢", "needs_fix": "🔴"}


# ------------- widget-key helpers ----------------------------------------

def kq(uid: int, field: str) -> str:
    return f"q{uid}_{field}"


def kr(uid: int, j: int, field: str) -> str:
    return f"q{uid}_r{j}_{field}"


def _next_uid() -> int:
    st.session_state.uid_counter += 1
    return st.session_state.uid_counter


def _next_question_id() -> str:
    nums = []
    for q in st.session_state.questions:
        qid = st.session_state.get(kq(q["_uid"], "id"), "")
        if qid.startswith("P-"):
            try:
                nums.append(int(qid[2:]))
            except ValueError:
                pass
    return f"P-{(max(nums) + 1 if nums else 1):03d}"


# ------------- state init ------------------------------------------------

if "questions" not in st.session_state:
    st.session_state.questions = []
if "uid_counter" not in st.session_state:
    st.session_state.uid_counter = 0
if "trial_id" not in st.session_state:
    st.session_state.trial_id = ""
if "username" not in st.session_state:
    st.session_state.username = ""
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "versions" not in st.session_state:
    st.session_state.versions = []
if "pair_reviews" not in st.session_state:
    st.session_state.pair_reviews = []
if "loaded_version" not in st.session_state:
    st.session_state.loaded_version = ""


# ------------- callbacks -------------------------------------------------

def _add_question() -> None:
    new_id = _next_question_id()
    uid = _next_uid()
    st.session_state.questions.append({"_uid": uid, "rubrics": []})
    st.session_state[kq(uid, "id")] = new_id


def _remove_question(idx: int) -> None:
    st.session_state.questions.pop(idx)


def _on_type_change(uid: int) -> None:
    """Regenerate rubric structure (and seed blank value fields) on type change."""
    qt = st.session_state.get(kq(uid, "qt"), "")
    q = next((x for x in st.session_state.questions if x["_uid"] == uid), None)
    if q is None:
        return
    # Clear old rubric value fields.
    for j in range(len(q["rubrics"])):
        for f in ("points", "tolerance", "criterion"):
            st.session_state.pop(kr(uid, j, f), None)
    # New structure (artifact + dimension fixed by type).
    new_rubrics = [
        {"artifact": r["artifact"], "dimension": r["dimension"]}
        for r in rubrics_for_type(qt)
    ]
    q["rubrics"] = new_rubrics
    for j in range(len(new_rubrics)):
        for f in ("points", "tolerance", "criterion"):
            st.session_state[kr(uid, j, f)] = ""


def _build_prompts() -> list:
    """Assemble the questions payload from session_state (the source of truth)."""
    prompts = []
    for q in st.session_state.questions:
        uid = q["_uid"]
        de = st.session_state.get(kq(uid, "de"), "")
        prompts.append(
            {
                "id": st.session_state.get(kq(uid, "id"), ""),
                "design_element": de,
                "design_element_other": (
                    st.session_state.get(kq(uid, "deother"), "") if de == "Others" else ""
                ),
                "question": st.session_state.get(kq(uid, "question"), ""),
                "question_type": st.session_state.get(kq(uid, "qt"), ""),
                "rubrics": [
                    {
                        "artifact": rub["artifact"],
                        "dimension": rub["dimension"],
                        "points": st.session_state.get(kr(uid, j, "points"), ""),
                        "tolerance": st.session_state.get(kr(uid, j, "tolerance"), ""),
                        "criterion": st.session_state.get(kr(uid, j, "criterion"), ""),
                    }
                    for j, rub in enumerate(q["rubrics"])
                ],
            }
        )
    return prompts


def _save_draft() -> None:
    st.session_state.last_result = {"kind": "draft", "msg": "Draft saved in this browser session."}


def _find_versions() -> None:
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

    new_questions = []
    for qp in prompts:
        uid = _next_uid()
        rubrics = [
            {"artifact": r.get("artifact", ""), "dimension": r.get("dimension", "")}
            for r in (qp.get("rubrics") or [])
        ]
        new_questions.append({"_uid": uid, "rubrics": rubrics})
        st.session_state[kq(uid, "id")] = qp.get("id", "")
        st.session_state[kq(uid, "de")] = qp.get("design_element", "")
        st.session_state[kq(uid, "deother")] = qp.get("design_element_other", "")
        st.session_state[kq(uid, "qt")] = qp.get("question_type", "")
        st.session_state[kq(uid, "question")] = qp.get("question", "")
        for j, r in enumerate(qp.get("rubrics") or []):
            st.session_state[kr(uid, j, "points")] = r.get("points", "")
            st.session_state[kr(uid, j, "tolerance")] = r.get("tolerance", "")
            st.session_state[kr(uid, j, "criterion")] = r.get("criterion", "")

    st.session_state.questions = new_questions
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
        "prompts": _build_prompts(),
    }
    try:
        result = save_submission(trial_id, username, comparison)
        st.session_state.last_result = {
            "kind": "success",
            "msg": f"Saved as new version `{result['version']}`. "
            "Use “Find versions” to see all versions.",
            "url": result.get("url"),
        }
        try:
            st.session_state.versions = list_versions(trial_id, username)
        except Exception:
            pass
    except Exception as e:
        st.session_state.last_result = {"kind": "error", "msg": f"Submit failed: {e}"}


def _render_review_lines(reviews: list) -> None:
    """Render reviews as markdown bullets, newest first (with version tag)."""
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


# ------------- PDF reference panel ---------------------------------------

@st.cache_data(show_spinner=False)
def _load_nct_map() -> dict:
    p = Path(__file__).parent / "assets" / "nct_to_docs.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pdf_url(doc: str, kind: str) -> str:
    return (
        f"https://huggingface.co/datasets/{SOURCE_REPO}"
        f"/resolve/main/documents/{doc}/{kind}.pdf"
    )


@st.cache_data(show_spinner=False)
def _fetch_pdf(url: str) -> bytes:
    import requests

    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


@st.cache_data(show_spinner=False)
def _pdf_b64(url: str) -> str:
    import base64

    return base64.b64encode(_fetch_pdf(url)).decode("ascii")


def render_pdf_panel() -> None:
    """Left-hand panel: show the trial's SAP / protocol PDF for reference.

    The PDF is fetched server-side and embedded as a base64 data: URI — we do
    NOT iframe huggingface.co directly, because HF sends X-Frame-Options and
    Chrome blocks that ("This page has been blocked by Chrome").
    """
    st.markdown("#### 📄 Reference document")
    trial_id = st.session_state.get("trial_id", "").strip()
    if not trial_id:
        st.caption("Enter a trial_id (on the right) to load its SAP / protocol PDF.")
        return
    docs = _load_nct_map().get(trial_id, [])
    if not docs:
        st.caption(f"No source document is mapped for `{trial_id}`.")
        manual = st.text_input("Document id (DOI folder), if you know it", key="pdf_manual_doc")
        if not manual.strip():
            return
        docs = [manual.strip()]
    doc = docs[0] if len(docs) == 1 else st.selectbox("Document", docs, key="pdf_doc")
    kind = st.radio("File", ["sap", "protocol"], horizontal=True, key="pdf_kind")
    url = _pdf_url(doc, kind)
    st.markdown(f"[Open {kind}.pdf in a new tab ↗]({url})")

    try:
        b64 = _pdf_b64(url)
    except Exception as e:
        st.warning(f"Could not load this PDF ({e}). Use the link above.")
        return

    components.html(
        f'<iframe src="data:application/pdf;base64,{b64}" '
        f'width="100%" height="820" style="border:1px solid #ddd;"></iframe>',
        height=840,
    )
    st.download_button(
        f"Download {kind}.pdf",
        data=_fetch_pdf(url),
        file_name=f"{doc}__{kind}.pdf",
        mime="application/pdf",
    )


# ------------- form ------------------------------------------------------

def render_form() -> None:
    # top fields
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

    # Overall reviewer feedback across all versions (per-question feedback is
    # shown inside each question block below).
    overall_history = [r for r in st.session_state.pair_reviews if not r.get("question_id")]
    if overall_history:
        with st.container(border=True):
            st.markdown(f"**Overall reviewer feedback — all versions ({len(overall_history)})**")
            _render_review_lines(overall_history)

    st.divider()

    # questions list
    st.subheader("Questions")
    if not st.session_state.questions:
        st.caption('No questions yet. Click "Add question" below to begin.')

    de_options = [""] + DESIGN_ELEMENTS
    qt_options = [""] + QUESTION_TYPES

    for i, q in enumerate(st.session_state.questions):
        uid = q["_uid"]
        with st.container(border=True):
            head_l, head_r = st.columns([6, 1])
            with head_l:
                st.text_input("id", key=kq(uid, "id"), label_visibility="collapsed")
            with head_r:
                st.button("Remove", key=f"rm_{uid}", on_click=_remove_question, args=(i,))

            col1, col2 = st.columns(2)
            with col1:
                st.selectbox(
                    "design_element",
                    options=de_options,
                    key=kq(uid, "de"),
                    format_func=lambda x: "— select —" if x == "" else x,
                )
                if st.session_state.get(kq(uid, "de")) == "Others":
                    st.text_input("Specify other design element", key=kq(uid, "deother"))
            with col2:
                st.selectbox(
                    "question_type",
                    options=qt_options,
                    key=kq(uid, "qt"),
                    format_func=lambda x: "— select —" if x == "" else x,
                    on_change=_on_type_change,
                    args=(uid,),
                )

            st.text_input(
                "question", key=kq(uid, "question"), placeholder="e.g., Alpha allocated to PFS"
            )

            # Reviewer feedback for this question across all versions of the trial.
            qid_val = st.session_state.get(kq(uid, "id"), "")
            q_reviews = [
                r for r in st.session_state.pair_reviews if r.get("question_id") == qid_val
            ]
            if q_reviews:
                latest_status = q_reviews[-1].get("status", "")  # oldest-first list
                if latest_status == "reviewed":
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
                for j, rub in enumerate(q["rubrics"]):
                    with st.container(border=True):
                        meta_parts = [f"**Artifact:** `{rub['artifact']}`"]
                        if rub["dimension"]:
                            meta_parts.append(f"**Dimension:** {rub['dimension']}")
                        st.markdown(" · ".join(meta_parts))

                        rc1, rc2 = st.columns(2)
                        with rc1:
                            st.text_input("points", key=kr(uid, j, "points"))
                        with rc2:
                            st.text_input("tolerance", key=kr(uid, j, "tolerance"))
                        st.text_area("criterion", key=kr(uid, j, "criterion"), height=80)

    st.button("+ Add question", on_click=_add_question)

    st.divider()

    # actions
    action_l, action_r = st.columns([1, 1])
    with action_l:
        st.button("Save draft", on_click=_save_draft, use_container_width=True)
    with action_r:
        st.button("Submit", on_click=_submit, type="primary", use_container_width=True)

    # status banner
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
                    "prompts": _build_prompts(),
                },
                indent=2,
                ensure_ascii=False,
            ),
            language="json",
        )


# ------------- layout ----------------------------------------------------

st.title("Trial Design Benchmark")
st.caption("Statistician intake form")
if not hf_configured:
    st.info(
        "ℹ️ HF env vars not set — submissions will be written to `./data/submissions/` "
        "(local dev mode)."
    )

col_pdf, col_form = st.columns([5, 7], gap="large")
with col_pdf:
    render_pdf_panel()
with col_form:
    render_form()
