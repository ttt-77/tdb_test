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

        # ---- Review history across ALL versions of this trial --------
        all_reviews = s.get("all_reviews") or []
        current_version = s.get("version", "")
        st.markdown(f"#### Review history — all versions ({len(all_reviews)})")
        if not all_reviews:
            st.caption("No reviews yet.")
        else:
            for rev in reversed(all_reviews):  # newest first
                rev_version = rev.get("version", "")
                is_current = rev_version == current_version
                vtag = f"`v{rev_version}`" + ("  _(current)_" if is_current else "")
                st.markdown(
                    f"- {status_badge(rev.get('status', ''))} — "
                    f"**{rev.get('reviewer') or 'anon'}** "
                    f"· _{rev.get('at', '')}_ · on {vtag}"
                    + (f"  \n  Reviews: {rev.get('note')}" if rev.get("note") else "")
                )

        # ---- Add a review (applies to the latest version) -----------
        st.markdown(f"#### Add a review — on latest version `v{s.get('version', '')}`")
        with st.form(f"review_{s['submissionId']}"):
            new_status = st.radio(
                "Status",
                options=VALID_STATUSES,
                index=VALID_STATUSES.index(s.get("status", "pending"))
                if s.get("status") in VALID_STATUSES
                else 0,
                horizontal=True,
            )
            rc1, rc2 = st.columns([1, 2])
            with rc1:
                reviewer = st.text_input("Your name", placeholder="e.g., Dr. Smith")
            with rc2:
                note = st.text_input("Comment", placeholder="optional")
            if st.form_submit_button("Add review", type="primary"):
                if not reviewer.strip():
                    st.error("Please enter your name.")
                else:
                    try:
                        add_review(
                            s["submissionId"],
                            status=new_status,
                            reviewer=reviewer.strip(),
                            note=note.strip(),
                        )
                        st.success("Review added.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to add review: {e}")

        with st.expander("Raw submission JSON"):
            st.code(json.dumps(s.get("submission", {}), indent=2, ensure_ascii=False), language="json")
