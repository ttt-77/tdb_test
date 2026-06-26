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

import csv
import json
from pathlib import Path

import streamlit as st

from lib.schema import (
    DESIGN_ELEMENTS,
    IMPORTANCE_OPTIONS,
    QUESTION_TYPES,
    dimensions_for_type,
)
from lib.storage import (
    get_submission,
    hf_configured,
    list_versions,
    pair_reviews,
    save_submission,
)

st.set_page_config(page_title="TDB Intake", page_icon="🔬", layout="centered")

SOURCE_REPO = "trialdesignbench/source"

# st.fragment (Streamlit >=1.37) isolates reruns; fall back to a no-op on older
# versions so the app still runs (just without the perf isolation).
fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
if fragment is None:
    def fragment(func):  # type: ignore
        return func

_STATUS_EMOJI = {"pending": "🟡", "reviewed": "🟢", "needs_fix": "🔴"}
DEFAULT_IMPORTANCE = "Medium"


# ------------- widget-key helpers ----------------------------------------

def kq(uid: int, field: str) -> str:
    return f"q{uid}_{field}"


def kc(uid: int, j: int, cid: int, field: str) -> str:
    """Key for a criterion field: question uid, dimension index j, criterion id."""
    return f"q{uid}_r{j}_c{cid}_{field}"


def _next_uid() -> int:
    st.session_state.uid_counter += 1
    return st.session_state.uid_counter


def _next_cid() -> int:
    st.session_state.cid_counter += 1
    return st.session_state.cid_counter


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
if "cid_counter" not in st.session_state:
    st.session_state.cid_counter = 0
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


def _clear_criterion_keys(uid: int, j: int, cid: int) -> None:
    for f in ("criterion", "importance"):
        st.session_state.pop(kc(uid, j, cid, f), None)


def _on_type_change(uid: int) -> None:
    """Rebuild the dimension blocks (each with one starter criterion) on type change."""
    qt = st.session_state.get(kq(uid, "qt"), "")
    q = next((x for x in st.session_state.questions if x["_uid"] == uid), None)
    if q is None:
        return
    # Clear all existing criterion fields.
    for j, rub in enumerate(q["rubrics"]):
        for cid in rub.get("criteria", []):
            _clear_criterion_keys(uid, j, cid)
    # New dimension blocks (artifact + dimension fixed by type), each seeded
    # with its default number of criterion rows (e.g. Method shows 3).
    new_rubrics = []
    for j, dim in enumerate(dimensions_for_type(qt)):
        n = max(1, int(dim.get("default_criteria", 1)))
        cids = []
        for _ in range(n):
            cid = _next_cid()
            st.session_state[kc(uid, j, cid, "importance")] = DEFAULT_IMPORTANCE
            cids.append(cid)
        new_rubrics.append(
            {"artifact": dim["artifact"], "dimension": dim["dimension"], "criteria": cids}
        )
    q["rubrics"] = new_rubrics


def _add_criterion(uid: int, j: int) -> None:
    q = next((x for x in st.session_state.questions if x["_uid"] == uid), None)
    if q is None or j >= len(q["rubrics"]):
        return
    cid = _next_cid()
    st.session_state[kc(uid, j, cid, "importance")] = DEFAULT_IMPORTANCE
    q["rubrics"][j]["criteria"].append(cid)


def _remove_criterion(uid: int, j: int, cid: int) -> None:
    q = next((x for x in st.session_state.questions if x["_uid"] == uid), None)
    if q is None or j >= len(q["rubrics"]):
        return
    crits = q["rubrics"][j]["criteria"]
    if cid in crits:
        crits.remove(cid)
        _clear_criterion_keys(uid, j, cid)


def _build_prompts() -> list:
    """Assemble the questions payload from session_state (the source of truth)."""
    prompts = []
    for q in st.session_state.questions:
        uid = q["_uid"]
        de = st.session_state.get(kq(uid, "de"), "")
        rubrics = []
        for j, rub in enumerate(q["rubrics"]):
            criteria = []
            for cid in rub.get("criteria", []):
                text = st.session_state.get(kc(uid, j, cid, "criterion"), "").strip()
                if not text:
                    continue  # skip empty/unfilled criteria (e.g. optional rows)
                criteria.append(
                    {
                        "criterion": text,
                        "importance": st.session_state.get(
                            kc(uid, j, cid, "importance"), DEFAULT_IMPORTANCE
                        ),
                    }
                )
            rubrics.append(
                {"artifact": rub["artifact"], "dimension": rub["dimension"], "criteria": criteria}
            )
        prompts.append(
            {
                "id": st.session_state.get(kq(uid, "id"), ""),
                "design_element": de,
                "design_element_other": (
                    st.session_state.get(kq(uid, "deother"), "") if de == "Others" else ""
                ),
                "question": st.session_state.get(kq(uid, "question"), ""),
                "question_type": st.session_state.get(kq(uid, "qt"), ""),
                "rubrics": rubrics,
            }
        )
    return prompts


def _save_draft() -> None:
    st.session_state.last_result = {"kind": "draft", "msg": "Draft saved in this browser session."}


def _find_versions() -> None:
    trial_id = st.session_state.trial_id.strip().lower()  # DOI is case-insensitive
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
        st.session_state[kq(uid, "id")] = qp.get("id", "")
        st.session_state[kq(uid, "de")] = qp.get("design_element", "")
        st.session_state[kq(uid, "deother")] = qp.get("design_element_other", "")
        st.session_state[kq(uid, "qt")] = qp.get("question_type", "")
        st.session_state[kq(uid, "question")] = qp.get("question", "")

        rubrics = []
        for j, r in enumerate(qp.get("rubrics") or []):
            # New format has r["criteria"]; old format had a single
            # points/criterion on the rubric itself.
            saved_crits = r.get("criteria")
            if saved_crits is None:
                saved_crits = [
                    {
                        "criterion": r.get("criterion", ""),
                        "importance": DEFAULT_IMPORTANCE,
                    }
                ]
            cids = []
            for c in saved_crits:
                cid = _next_cid()
                cids.append(cid)
                st.session_state[kc(uid, j, cid, "criterion")] = c.get("criterion", "")
                imp = str(c.get("importance", "")).strip()
                match = next(
                    (o for o in IMPORTANCE_OPTIONS if o.lower() == imp.lower()),
                    DEFAULT_IMPORTANCE,
                )
                st.session_state[kc(uid, j, cid, "importance")] = match
            rubrics.append(
                {"artifact": r.get("artifact", ""), "dimension": r.get("dimension", ""), "criteria": cids}
            )
        new_questions.append({"_uid": uid, "rubrics": rubrics})

    st.session_state.questions = new_questions
    st.session_state.loaded_version = record.get("version", "")
    st.session_state.last_result = {
        "kind": "success",
        "msg": f"Loaded version {record.get('version', '')} "
        f"({len(prompts)} question(s)). Edit and Submit to save a new version.",
    }


def _submit() -> None:
    trial_id = st.session_state.trial_id.strip().lower()  # DOI is case-insensitive
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

def _pdf_url(doc: str, kind: str) -> str:
    return (
        f"https://huggingface.co/datasets/{SOURCE_REPO}"
        f"/resolve/main/documents/{doc}/{kind}.pdf"
    )


def render_pdf_panel() -> None:
    """Left-hand panel: links to the document's SAP / protocol PDF.

    The entered trial_id is used directly as the document id (e.g.
    ``10.1200_jco.22.01989``) so there's no ambiguous NCT->document mapping.
    We only link out (open in a new tab) — embedding HF PDFs inline is blocked
    by X-Frame-Options and re-sending bytes made the form laggy.
    """
    st.markdown("#### 📄 Reference document")
    doc = st.session_state.get("trial_id", "").strip().lower()  # DOI is case-insensitive
    if not doc:
        st.caption(
            "Enter the DOI (e.g. `10.1200_jco.22.01989`) on the right "
            "to get its PDF links."
        )
        return
    sap_url = _pdf_url(doc, "sap")
    proto_url = _pdf_url(doc, "protocol")
    st.markdown(
        f'<a href="{sap_url}" target="_blank" rel="noopener">📄 Open sap.pdf in a new tab ↗</a>'
        "<br>"
        f'<a href="{proto_url}" target="_blank" rel="noopener">📄 Open protocol.pdf in a new tab ↗</a>',
        unsafe_allow_html=True,
    )
    st.caption(f"Document: `{doc}`")


# ------------- trial browser ---------------------------------------------

TRIAL_COLS = [
    "DOI",
    "Journal",
    "Year",
    "Therapeutic Area",
    "Phase",
    "Paper Title",
    "Paper Link",
]


@st.cache_data(show_spinner=False)
def load_trials() -> list:
    p = Path(__file__).parent / "assets" / "trials.csv"
    try:
        with open(p, encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


@fragment
def render_trial_browser() -> None:
    """Searchable table of trials (from tdr.parquet). Each column has its own
    search box; copy a `DOI` into the DOI field to load that trial."""
    trials = load_trials()
    if not trials:
        return
    with st.expander(f"🔎 Browse trials ({len(trials)}) — search to find a DOI"):
        queries = {}
        r1 = st.columns(4)
        for col, c in zip(TRIAL_COLS[:4], r1):
            with c:
                queries[col] = st.text_input(col, key=f"tsearch_{col}", placeholder="search…")
        r2 = st.columns(3)
        for col, c in zip(TRIAL_COLS[4:], r2):
            with c:
                queries[col] = st.text_input(col, key=f"tsearch_{col}", placeholder="search…")

        def _match(row: dict) -> bool:
            for col, q in queries.items():
                q = (q or "").strip().lower()
                if q and q not in str(row.get(col, "")).lower():
                    return False
            return True

        filtered = [r for r in trials if _match(r)]
        st.caption(f"{len(filtered)} match(es). Copy a `DOI` into the DOI field above.")
        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
            height=360,
            column_config={
                "Paper Link": st.column_config.LinkColumn("Paper Link", display_text="link ↗"),
            },
        )


# ------------- form ------------------------------------------------------

@fragment
def _questions_fragment() -> None:
    """The questions editor + actions. Runs as a fragment so frequent edits
    here don't trigger a full-app rerun (which would re-send the PDF)."""
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
                    "Design element",
                    options=de_options,
                    key=kq(uid, "de"),
                    format_func=lambda x: "— select —" if x == "" else x,
                )
                if st.session_state.get(kq(uid, "de")) == "Others":
                    st.text_input("Specify other design element", key=kq(uid, "deother"))
            with col2:
                st.selectbox(
                    "Question type",
                    options=qt_options,
                    key=kq(uid, "qt"),
                    format_func=lambda x: "— select —" if x == "" else x,
                    on_change=_on_type_change,
                    args=(uid,),
                )

            st.text_input(
                "Question", key=kq(uid, "question"), placeholder="e.g., Alpha allocated to PFS"
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

                        criteria = rub.get("criteria", [])
                        for ci, cid in enumerate(criteria):
                            label = f"Criterion {ci + 1}" + (" (optional)" if ci > 0 else "")
                            st.text_area(
                                label,
                                key=kc(uid, j, cid, "criterion"),
                                height=70,
                            )
                            cc1, cc2 = st.columns([3, 1])
                            with cc1:
                                st.selectbox(
                                    "Importance",
                                    options=IMPORTANCE_OPTIONS,
                                    key=kc(uid, j, cid, "importance"),
                                )
                            with cc2:
                                st.write("")
                                st.write("")
                                st.button(
                                    "✕",
                                    key=f"rmc_{uid}_{j}_{cid}",
                                    help="Remove this criterion",
                                    on_click=_remove_criterion,
                                    args=(uid, j, cid),
                                )
                        st.button(
                            "+ Add criterion",
                            key=f"addc_{uid}_{j}",
                            on_click=_add_criterion,
                            args=(uid, j),
                        )

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


def render_form() -> None:
    # top fields
    c1, c2 = st.columns(2)
    with c1:
        st.text_input(
            "DOI",
            key="trial_id",
            placeholder="e.g., 10.1200_jco.22.01989",
            help="Document id (DOI folder) — used to load the SAP/protocol PDF.",
        )
    with c2:
        st.text_input("Username", key="username", placeholder="e.g., jdoe")

    render_trial_browser()

    st.button(
        "Find versions",
        on_click=_find_versions,
        help="List all previously submitted versions for this trial_id + username.",
    )

    # Reference document PDF links (directly under Find versions).
    render_pdf_panel()

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

    # Questions + actions live in a fragment so editing them reruns only this
    # part — NOT the heavy PDF panel on the left (avoids re-sending the PDF).
    _questions_fragment()


# ------------- layout ----------------------------------------------------

st.title("Trial Design Benchmark")
st.caption("Statistician intake form")
if not hf_configured:
    st.info(
        "ℹ️ HF env vars not set — submissions will be written to `./data/submissions/` "
        "(local dev mode)."
    )

render_form()
