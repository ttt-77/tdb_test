"""Admin review console — list submissions, view review timeline, add reviews.

Submissions are immutable. Each review is stored as its own file under
``reviews/<submission>/``; a submission can be reviewed many times by different
people. The "current status" shown here is the most recent review's status.
"""

from __future__ import annotations

import json

import streamlit as st

from lib.schema import VALID_STATUSES
from lib.storage import (
    ADMIN_PASSWORD,
    add_review,
    check_admin_password,
    hf_configured,
    list_submissions,
)

st.set_page_config(page_title="TDB Intake — Admin", page_icon="🔍", layout="wide")

STATUS_EMOJI = {
    "pending": "🟡",
    "reviewed": "🟢",
    "needs_fix": "🔴",
}


def status_badge(status: str) -> str:
    return f"{STATUS_EMOJI.get(status, '⚪')} {status}"


def _md_cell(s: str) -> str:
    """Escape a value for use inside a markdown table cell."""
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def _render_question_reviews(qreviews: list, current_version: str) -> None:
    """Show the review thread for a single question (newest first)."""
    if not qreviews:
        st.caption("No reviews for this question yet.")
        return
    for rev in reversed(qreviews):
        rev_version = rev.get("version", "")
        cur = "  _(current)_" if rev_version == current_version else ""
        st.markdown(
            f"- {status_badge(rev.get('status', ''))} — "
            f"**{rev.get('reviewer') or 'anon'}** · _{rev.get('at', '')}_ "
            f"· on `v{rev_version}`{cur}"
            + (f"  \n  Reviews: {rev.get('note')}" if rev.get("note") else "")
        )


NO_REVIEW = "— no review —"
REVIEW_OPTIONS = [NO_REVIEW] + VALID_STATUSES


def render_questions(
    submission: dict, all_reviews: list, submission_id: str, current_version: str
) -> None:
    """Render the questionnaire (app layout) plus, for each question, its review
    thread and review *input* widgets. No submit here — a single batch submit
    button (below, in the enclosing form) commits everything at once.
    """
    prompts = (submission.get("comparison") or {}).get("prompts") or []
    st.markdown(f"#### Questions ({len(prompts)})")
    if not prompts:
        st.caption("No questions in this submission.")
        return

    for q in prompts:
        qid = q.get("id", "")
        with st.container(border=True):
            st.markdown(f"**`{qid}`**")
            c1, c2 = st.columns(2)
            with c1:
                de = q.get("design_element", "") or "—"
                if q.get("design_element") == "Others" and q.get("design_element_other"):
                    de = f"Others: {q['design_element_other']}"
                st.text_input(
                    "design_element", value=de, disabled=True,
                    key=f"de_{submission_id}_{qid}",
                )
            with c2:
                st.text_input(
                    "question_type", value=q.get("question_type", "") or "—",
                    disabled=True, key=f"qt_{submission_id}_{qid}",
                )
            st.text_input(
                "question", value=q.get("question", ""), disabled=True,
                key=f"qq_{submission_id}_{qid}",
            )

            rubrics = q.get("rubrics") or []
            if rubrics:
                st.markdown(f"**Rubrics ({len(rubrics)})**")
                for j, r in enumerate(rubrics):
                    with st.container(border=True):
                        meta = f"**Artifact:** `{r.get('artifact', '')}`"
                        if r.get("dimension"):
                            meta += f" · **Dimension:** {r['dimension']}"
                        st.markdown(meta)
                        rc1, rc2 = st.columns(2)
                        with rc1:
                            st.text_input(
                                "points", value=r.get("points", ""), disabled=True,
                                key=f"pt_{submission_id}_{qid}_{j}",
                            )
                        with rc2:
                            st.text_input(
                                "tolerance", value=r.get("tolerance", ""), disabled=True,
                                key=f"to_{submission_id}_{qid}_{j}",
                            )
                        st.text_area(
                            "criterion", value=r.get("criterion", ""), disabled=True,
                            key=f"cr_{submission_id}_{qid}_{j}", height=70,
                        )

            # ---- existing reviews for this question ----
            qreviews = [r for r in all_reviews if r.get("question_id") == qid]
            st.markdown(f"**Reviews for this question ({len(qreviews)})**")
            _render_question_reviews(qreviews, current_version)

            # ---- review input for this question (committed by batch submit) ----
            rc1, rc2 = st.columns([1, 2])
            with rc1:
                st.selectbox(
                    "Review status", options=REVIEW_OPTIONS,
                    key=f"revst_{submission_id}_{qid}",
                )
            with rc2:
                st.text_input("Comment", key=f"revnt_{submission_id}_{qid}")


# ------------- auth ------------------------------------------------------

if "admin_authed" not in st.session_state:
    st.session_state.admin_authed = False

st.title("Admin — Review submissions")

if not ADMIN_PASSWORD:
    st.warning("`ADMIN_PASSWORD` is not set on the server — admin is open to anyone.")
    st.session_state.admin_authed = True

if not st.session_state.admin_authed:
    with st.form("auth"):
        pw = st.text_input("Admin password", type="password")
        if st.form_submit_button("Continue"):
            if check_admin_password(pw):
                st.session_state.admin_authed = True
                st.rerun()
            else:
                st.error("Wrong password.")
    st.stop()

# ------------- load list -------------------------------------------------

if not hf_configured:
    st.info("ℹ️ Reading from `./data/` (local dev mode).")

cols = st.columns([3, 1])
with cols[0]:
    status_filter = st.multiselect(
        "Filter by status",
        options=VALID_STATUSES,
        default=[],
        placeholder="Show all",
    )
with cols[1]:
    if st.button("Refresh", use_container_width=True):
        st.rerun()

try:
    items = list_submissions()
except Exception as e:
    st.error(f"Failed to list submissions: {e}")
    st.stop()

if status_filter:
    items = [s for s in items if s["status"] in status_filter]

st.caption(f"{len(items)} trial(s) — showing the latest version of each")

if not items:
    st.info("No submissions match this filter.")
    st.stop()

# ------------- list display ----------------------------------------------

for s in items:
    n_reviews = s.get("review_count", 0)
    review_tag = f"  ·  💬 {n_reviews}" if n_reviews else "  ·  no reviews yet"
    label = (
        f"{STATUS_EMOJI.get(s['status'], '⚪')} "
        f"**{s['trial_id']}** — {s['username']}  ·  "
        f"_{s['submittedAt']}_{review_tag}"
    )
    with st.expander(label):
        meta_c1, meta_c2 = st.columns(2)
        with meta_c1:
            st.markdown(
                f"**Current status:** {status_badge(s.get('status', 'pending'))}  \n"
                f"**Submitted:** {s.get('submittedAt', '')}  \n"
                f"**Last reviewed:** {s.get('reviewedAt', '') or '—'}"
            )
        with meta_c2:
            st.markdown(
                f"**Latest version:** `{s.get('version', '')}`  \n"
                f"**Last reviewer:** {s.get('reviewer', '') or '—'}"
            )

        all_reviews = s.get("all_reviews") or []
        current_version = s.get("version", "")
        sid = s["submissionId"]
        prompts = (s.get("submission", {}).get("comparison") or {}).get("prompts") or []

        # ---- Overall review history (whole-submission reviews) -------
        overall_reviews = [r for r in all_reviews if not r.get("question_id")]
        st.markdown(f"#### Overall review history — all versions ({len(overall_reviews)})")
        if not overall_reviews:
            st.caption("No overall reviews yet.")
        else:
            for rev in reversed(overall_reviews):  # newest first
                rev_version = rev.get("version", "")
                is_current = rev_version == current_version
                vtag = f"`v{rev_version}`" + ("  _(current)_" if is_current else "")
                st.markdown(
                    f"- {status_badge(rev.get('status', ''))} — "
                    f"**{rev.get('reviewer') or 'anon'}** "
                    f"· _{rev.get('at', '')}_ · on {vtag}"
                    + (f"  \n  Reviews: {rev.get('note')}" if rev.get("note") else "")
                )

        # ---- One batch form: fill every question + overall, submit once ----
        st.markdown("### Review")
        st.caption(
            "Set a status and comment on any questions you want to review, plus an "
            "optional overall review, then click **Submit all reviews** at the bottom. "
            "Leave a status as “— no review —” to skip it."
        )
        with st.form(f"reviewform_{sid}"):
            reviewer = st.text_input(
                "Your name (applies to all reviews below)", placeholder="e.g., Dr. Smith"
            )

            # Questionnaire (app layout) + per-question review inputs.
            render_questions(
                s.get("submission", {}),
                all_reviews=all_reviews,
                submission_id=sid,
                current_version=current_version,
            )

            # Overall review input.
            st.markdown("#### Overall review (whole submission)")
            oc1, oc2 = st.columns([1, 2])
            with oc1:
                st.selectbox("Overall status", options=REVIEW_OPTIONS, key=f"revst_{sid}__overall")
            with oc2:
                st.text_input("Overall comment", key=f"revnt_{sid}__overall")

            submitted = st.form_submit_button("Submit all reviews", type="primary")

        if submitted:
            name = reviewer.strip()
            # Gather every question + overall input that has a chosen status.
            to_add = []
            for q in prompts:
                qid = q.get("id", "")
                stt = st.session_state.get(f"revst_{sid}_{qid}", NO_REVIEW)
                if stt and stt != NO_REVIEW:
                    note = st.session_state.get(f"revnt_{sid}_{qid}", "")
                    to_add.append((qid, stt, note))
            ov_stt = st.session_state.get(f"revst_{sid}__overall", NO_REVIEW)
            if ov_stt and ov_stt != NO_REVIEW:
                to_add.append(("", ov_stt, st.session_state.get(f"revnt_{sid}__overall", "")))

            if not to_add:
                st.warning("Nothing to submit — set a status on at least one question or overall.")
            elif not name:
                st.error("Please enter your name.")
            else:
                try:
                    for qid, stt, note in to_add:
                        add_review(
                            sid, status=stt, reviewer=name,
                            note=(note or "").strip(), question_id=qid,
                        )
                    # Reset the form inputs for this submission.
                    for q in prompts:
                        st.session_state.pop(f"revst_{sid}_{q.get('id', '')}", None)
                        st.session_state.pop(f"revnt_{sid}_{q.get('id', '')}", None)
                    st.session_state.pop(f"revst_{sid}__overall", None)
                    st.session_state.pop(f"revnt_{sid}__overall", None)
                    st.success(f"Submitted {len(to_add)} review(s).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to submit reviews: {e}")

        with st.expander("Raw submission JSON"):
            st.code(json.dumps(s.get("submission", {}), indent=2, ensure_ascii=False), language="json")
