"""Admin review console — list submissions, change status, add reviewer notes."""

from __future__ import annotations

import json

import streamlit as st

from lib.schema import VALID_STATUSES
from lib.storage import (
    ADMIN_PASSWORD,
    check_admin_password,
    get_submission,
    hf_configured,
    list_submissions,
    update_submission,
)

st.set_page_config(page_title="TDB Intake — Admin", page_icon="🔍", layout="wide")

STATUS_EMOJI = {
    "pending": "🟡",
    "reviewed": "🟢",
    "needs_fix": "🔴",
}

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
    st.info("ℹ️ Reading from `./data/submissions/` (local dev mode).")

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
    summaries = list_submissions()
except Exception as e:
    st.error(f"Failed to list submissions: {e}")
    st.stop()

if status_filter:
    summaries = [s for s in summaries if s["status"] in status_filter]

st.caption(f"{len(summaries)} submission(s)")

if not summaries:
    st.info("No submissions match this filter.")
    st.stop()

# ------------- list display ----------------------------------------------

for s in summaries:
    label = (
        f"{STATUS_EMOJI.get(s['status'], '⚪')} "
        f"**{s['trial_id']}** — {s['username']}  ·  "
        f"_{s['submittedAt']}_"
    )
    with st.expander(label):
        record = get_submission(s["submissionId"])
        if record is None:
            st.error("Could not fetch this record.")
            continue

        meta_c1, meta_c2 = st.columns(2)
        with meta_c1:
            st.markdown(
                f"**Status:** `{record.get('status', 'pending')}`  \n"
                f"**Submitted:** {record.get('submittedAt', '')}  \n"
                f"**Reviewed at:** {record.get('reviewedAt', '') or '—'}"
            )
        with meta_c2:
            st.markdown(
                f"**File:** `{record.get('submissionId', '')}`  \n"
                f"**Reviewer:** {record.get('reviewer', '') or '—'}  \n"
                f"**Reviewer note:** {record.get('reviewerNote', '') or '—'}"
            )

        # Update form
        with st.form(f"upd_{s['submissionId']}"):
            new_status = st.radio(
                "Set status",
                options=VALID_STATUSES,
                index=VALID_STATUSES.index(record.get("status", "pending"))
                if record.get("status") in VALID_STATUSES
                else 0,
                horizontal=True,
            )
            ur_c1, ur_c2 = st.columns(2)
            with ur_c1:
                new_reviewer = st.text_input(
                    "Reviewer name", value=record.get("reviewer", "")
                )
            with ur_c2:
                new_note = st.text_input(
                    "Reviewer note", value=record.get("reviewerNote", "")
                )
            if st.form_submit_button("Save", type="primary"):
                try:
                    update_submission(
                        s["submissionId"],
                        status=new_status,
                        reviewer=new_reviewer,
                        reviewer_note=new_note,
                    )
                    st.success("Saved.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Update failed: {e}")

        with st.expander("Raw submission JSON"):
            st.code(json.dumps(record, indent=2, ensure_ascii=False), language="json")
