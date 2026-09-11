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
import inspect
import json
from pathlib import Path

import streamlit as st

from lib.agent import SYSTEM_PROMPT
from lib.schema import (
    normalize_doi,
    DESIGN_ELEMENTS,
    IMPORTANCE_OPTIONS,
    QUESTION_TYPES,
    SCORING_OPTIONS,
    dimensions_for_type,
    example_import_json,
    validate_and_normalize_prompts,
)
from lib.ui import TEXTAREA_AUTOGROW_CSS, row_matches, textarea_height
from lib.storage import (
    get_draft,
    get_submission,
    hf_configured,
    list_reference_submissions,
    list_versions,
    pair_reviews,
    save_draft,
    save_submission,
)

st.set_page_config(page_title="TDB Intake", page_icon="🔬", layout="centered")
st.markdown(TEXTAREA_AUTOGROW_CSS, unsafe_allow_html=True)

SOURCE_REPO = "trialdesignbench/source"
DEMO_VIDEO_URL = (
    "https://drive.google.com/file/d/1RPhP2Fwrwqurc7Xrs38uXeBf3IwNk1wD/view"
)
SYSTEM_PROMPT_URL = (
    "https://github.com/ttt-77/tdb_test/blob/main/lib/agent.py#L21"
)

# st.fragment (Streamlit >=1.37) isolates reruns; fall back to a no-op on older
# versions so the app still runs (just without the perf isolation).
fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
if fragment is None:
    def fragment(func):  # type: ignore
        return func

_STATUS_EMOJI = {"pending": "🟡", "reviewed": "🟢", "needs_fix": "🔴"}
DEFAULT_IMPORTANCE = "Medium"
DEFAULT_SCORING = "Add"


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
if "import_uploader_nonce" not in st.session_state:
    st.session_state.import_uploader_nonce = 0
# After a JSON import the import panel asks (via this flag) to be collapsed and
# its uploader cleared on the *next* run — widget state can only be changed
# before the widget is instantiated.
if st.session_state.pop("_collapse_import_panel", False):
    st.session_state.show_import_json = False
    st.session_state.import_uploader_nonce += 1


# ------------- callbacks -------------------------------------------------

def _normalize_doi_input() -> None:
    """Canonicalize the DOI field as soon as it is edited: `10.1056/NEJMoa…`
    or a `https://doi.org/…` link becomes the stored form `10.1056_nejmoa…`."""
    raw = st.session_state.get("trial_id", "")
    norm = normalize_doi(raw)
    if norm != raw:
        st.session_state.trial_id = norm


def _add_question() -> None:
    new_id = _next_question_id()
    uid = _next_uid()
    st.session_state.questions.append({"_uid": uid, "rubrics": []})
    st.session_state[kq(uid, "id")] = new_id


def _remove_question(idx: int) -> None:
    st.session_state.questions.pop(idx)


def _clear_criterion_keys(uid: int, j: int, cid: int) -> None:
    for f in ("criterion", "importance", "scoring"):
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
            st.session_state[kc(uid, j, cid, "scoring")] = DEFAULT_SCORING
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
    st.session_state[kc(uid, j, cid, "scoring")] = DEFAULT_SCORING
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
                        "scoring": st.session_state.get(
                            kc(uid, j, cid, "scoring"), DEFAULT_SCORING
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
    trial_id = normalize_doi(st.session_state.trial_id)
    username = st.session_state.username.strip()
    if not trial_id or not username:
        st.session_state.last_result = {
            "at": "bottom",
            "kind": "error",
            "msg": "DOI and username are required to save a draft.",
        }
        return
    comparison = {"trial_id": trial_id, "username": username, "prompts": _build_prompts()}
    try:
        result = save_draft(trial_id, username, comparison)
        st.session_state.last_result = {
            "at": "bottom",
            "kind": "success",
            "msg": f"Draft saved as `{result['version']}`. Come back with the same "
            f"DOI + username and click “Load draft” to restore the latest draft.",
            "url": result.get("url"),
        }
    except Exception as e:
        st.session_state.last_result = {"at": "bottom", "kind": "error", "msg": f"Save draft failed: {e}"}


def _find_versions() -> None:
    trial_id = normalize_doi(st.session_state.trial_id)
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


def _populate_form(prompts: list) -> None:
    """Rebuild the form's questions from a saved prompts list (version or draft)."""
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
                    {"criterion": r.get("criterion", ""), "importance": DEFAULT_IMPORTANCE}
                ]
            cids = []
            for c in saved_crits:
                cid = _next_cid()
                cids.append(cid)
                st.session_state[kc(uid, j, cid, "criterion")] = c.get("criterion", "")
                imp = str(c.get("importance", "")).strip()
                st.session_state[kc(uid, j, cid, "importance")] = next(
                    (o for o in IMPORTANCE_OPTIONS if o.lower() == imp.lower()),
                    DEFAULT_IMPORTANCE,
                )
                sco = str(c.get("scoring", "")).strip()
                st.session_state[kc(uid, j, cid, "scoring")] = next(
                    (o for o in SCORING_OPTIONS if o.lower() == sco.lower()),
                    DEFAULT_SCORING,
                )
            rubrics.append(
                {"artifact": r.get("artifact", ""), "dimension": r.get("dimension", ""), "criteria": cids}
            )
        new_questions.append({"_uid": uid, "rubrics": rubrics})

    st.session_state.questions = new_questions


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
    _populate_form(prompts)
    st.session_state.loaded_version = record.get("version", "")
    st.session_state.last_result = {
        "kind": "success",
        "msg": f"Loaded version {record.get('version', '')} "
        f"({len(prompts)} question(s)). Edit and Submit to save a new version.",
    }


def _load_draft() -> None:
    trial_id = normalize_doi(st.session_state.trial_id)
    username = st.session_state.username.strip()
    if not trial_id or not username:
        st.session_state.last_result = {
            "kind": "error",
            "msg": "Enter DOI and username, then click Load draft.",
        }
        return
    try:
        record = get_draft(trial_id, username)
    except Exception as e:
        st.session_state.last_result = {"kind": "error", "msg": f"Load draft failed: {e}"}
        return
    if not record:
        st.session_state.last_result = {
            "kind": "info",
            "msg": f"No saved draft for `{trial_id}` / `{username}`.",
        }
        return
    prompts = (record.get("comparison") or {}).get("prompts") or []
    _populate_form(prompts)
    st.session_state.last_result = {
        "kind": "success",
        "msg": f"Loaded latest draft (saved {record.get('savedAt', '')}, "
        f"{len(prompts)} question(s)).",
    }


def _submit() -> None:
    trial_id = normalize_doi(st.session_state.trial_id)
    username = st.session_state.username.strip()
    if not trial_id or not username:
        st.session_state.last_result = {"at": "bottom", "kind": "error", "msg": "trial_id and username are required."}
        return
    comparison = {
        "trial_id": trial_id,
        "username": username,
        "prompts": _build_prompts(),
    }
    try:
        result = save_submission(trial_id, username, comparison)
        st.session_state.last_result = {
            "at": "bottom",
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
        st.session_state.last_result = {"at": "bottom", "kind": "error", "msg": f"Submit failed: {e}"}


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
    doc = normalize_doi(st.session_state.get("trial_id", ""))
    if not doc:
        st.caption(
            "Enter the DOI (e.g. `10.1200_jco.22.01989`) on the right "
            "to get its PDF links."
        )
        return
    known = valid_dois()
    if known and doc not in known:
        st.warning(
            f"No source document found for DOI `{doc}`. "
            "Check it, or use 🔎 Browse trials to find a valid DOI."
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
]


@st.cache_data(show_spinner=False)
def load_trials() -> list:
    p = Path(__file__).parent / "assets" / "trials.csv"
    try:
        with open(p, encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


@st.cache_data(show_spinner=False)
def valid_dois() -> set:
    """Lowercased set of DOIs that have a source document (from trials.csv)."""
    return {normalize_doi(r.get("DOI")) for r in load_trials() if r.get("DOI")}


@fragment
def render_trial_browser() -> None:
    """Searchable table of trials (from tdr.parquet). Each column has its own
    search box; copy a `DOI` into the DOI field to load that trial."""
    trials = load_trials()
    if not trials:
        return
    # Gate the (heavy) table behind a toggle so it renders ONLY when turned on.
    # An expander would render its contents on every rerun even while collapsed.
    if not st.toggle(f"🔎 Browse trials ({len(trials)}) — search to find a DOI",
                     key="show_trial_browser"):
        return

    gq = st.text_input(
        "🔍 Search all columns",
        key="tsearch_all",
        placeholder="e.g. oncology phase 3 nejm — every word must match somewhere in the row",
    )
    queries = {}
    with st.expander("Filter by column"):
        r1 = st.columns(3)
        for col, c in zip(TRIAL_COLS[:3], r1):
            with c:
                queries[col] = st.text_input(col, key=f"tsearch_{col}", placeholder="search…")
        r2 = st.columns(3)
        for col, c in zip(TRIAL_COLS[3:], r2):
            with c:
                queries[col] = st.text_input(col, key=f"tsearch_{col}", placeholder="search…")

    MAX_ROWS = 100
    filtered = [
        {c: r.get(c, "") for c in TRIAL_COLS}
        for r in trials
        if row_matches(r, gq, queries, TRIAL_COLS)
    ]
    shown = filtered[:MAX_ROWS]
    if len(filtered) > MAX_ROWS:
        st.caption(
            f"{len(filtered)} match(es); showing first {MAX_ROWS}. "
            "Refine the search to narrow. Copy a `DOI` into the DOI field above."
        )
    else:
        st.caption(f"{len(filtered)} match(es). Copy a `DOI` into the DOI field above.")
    st.dataframe(shown, use_container_width=True, hide_index=True, height=360)


# ------------- reference browser (other people's submissions) -------------

@st.cache_data(show_spinner=False, ttl=300)
def _load_reference_list() -> list:
    return list_reference_submissions()


def _render_reference_questions(prompts: list) -> None:
    """Read-only view of a submission's questions (markdown only — no widgets,
    so it can't collide with the editable form's keys)."""
    if not prompts:
        st.caption("This submission has no questions.")
        return
    for q in prompts:
        de = q.get("design_element", "")
        if de == "Others" and q.get("design_element_other"):
            de = f"Others: {q['design_element_other']}"
        with st.container(border=True):
            st.markdown(
                f"**`{q.get('id','')}` · {de or '—'} · `{q.get('question_type','') or '—'}`**"
            )
            st.markdown(f"> {q.get('question','') or '_(empty)_'}")
            for r in q.get("rubrics") or []:
                head = f"**Artifact:** `{r.get('artifact','')}`"
                if r.get("dimension"):
                    head += f" · **Dimension:** {r['dimension']}"
                st.markdown(head)
                crits = r.get("criteria")
                if crits is None:  # legacy single-criterion records
                    crits = [
                        {
                            "criterion": r.get("criterion", ""),
                            "importance": r.get("points", ""),
                            "scoring": "",
                        }
                    ]
                for i, c in enumerate(crits, 1):
                    bits = []
                    if c.get("importance"):
                        bits.append(f"importance: **{c['importance']}**")
                    if c.get("scoring"):
                        bits.append(f"scoring: **{c['scoring']}**")
                    line = f"&nbsp;&nbsp;{i}. {c.get('criterion','') or '—'}"
                    if bits:
                        line += "  \n&nbsp;&nbsp;&nbsp;&nbsp;_" + " · ".join(bits) + "_"
                    st.markdown(line)


# Search boxes for the reference table (matches the trial browser's UX).
REF_SEARCH_COLS = ["DOI", "Username", "Paper Title", "Therapeutic Area", "Phase", "Journal"]
REF_TABLE_COLS = [
    "Username", "DOI", "Paper Title", "Journal", "Year", "Therapeutic Area",
    "Phase", "Questions", "Versions", "Submitted",
]
_DF_SUPPORTS_SELECT = "on_select" in inspect.signature(st.dataframe).parameters


def _reference_rows() -> list:
    """Submissions joined with trials.csv so each row shows WHICH trial it is."""
    trials = {normalize_doi(t.get("DOI")): t for t in load_trials()}
    rows = []
    for r in _load_reference_list():
        t = trials.get(normalize_doi(r.get("trial_id")), {})
        rows.append(
            {
                "Username": r.get("username", ""),
                "DOI": r.get("trial_id", ""),
                "Paper Title": t.get("Paper Title", ""),
                "Journal": t.get("Journal", ""),
                "Year": t.get("Year", ""),
                "Therapeutic Area": t.get("Therapeutic Area", ""),
                "Phase": t.get("Phase", ""),
                "Questions": r.get("num_questions", 0),
                "Versions": r.get("num_versions", 0),
                "Submitted": (r.get("submittedAt") or "")[:16],
                "_id": r.get("submissionId", ""),
            }
        )
    return rows


@fragment
def render_reference_browser() -> None:
    """Browse other people's submitted forms as a reference / starting point.

    A searchable table (like the trial browser), joined with trials.csv so you
    can tell which trial each benchmark is for. Click a row to preview it and
    optionally copy its questions into your form.
    """
    if not st.toggle(
        "📚 See how others filled the form",
        key="show_reference_browser",
        help="Search submitted forms from other trials/users and use one as a reference.",
    ):
        return

    try:
        rows = _reference_rows()
    except Exception as e:
        st.error(f"Could not list submissions: {e}")
        return
    if not rows:
        st.caption("No submissions yet.")
        return

    # ---- global search (any column) + optional per-column filters ----
    gq = st.text_input(
        "🔍 Search all columns",
        key="refsearch_all",
        placeholder="e.g. ericz pembrolizumab phase 3 — every word must match somewhere in the row",
    )
    queries = {}
    with st.expander("Filter by column"):
        r1 = st.columns(3)
        for col, c in zip(REF_SEARCH_COLS[:3], r1):
            with c:
                queries[col] = st.text_input(col, key=f"refsearch_{col}", placeholder="search…")
        r2 = st.columns(3)
        for col, c in zip(REF_SEARCH_COLS[3:], r2):
            with c:
                queries[col] = st.text_input(col, key=f"refsearch_{col}", placeholder="search…")

    MAX_ROWS = 100
    filtered = [r for r in rows if row_matches(r, gq, queries, REF_TABLE_COLS)]
    shown = filtered[:MAX_ROWS]

    hc1, hc2 = st.columns([4, 1])
    with hc1:
        more = f"; showing first {MAX_ROWS} — refine the search" if len(filtered) > MAX_ROWS else ""
        st.caption(f"{len(filtered)} of {len(rows)} submission(s){more}. Click a row to preview it.")
    with hc2:
        if st.button("Refresh", use_container_width=True, key="reference_refresh"):
            _load_reference_list.clear()
            st.rerun()

    if not shown:
        return
    display = [{k: r.get(k, "") for k in REF_TABLE_COLS} for r in shown]

    # ---- pick a row: native row selection when available, else a selectbox ----
    picked = None
    if _DF_SUPPORTS_SELECT:
        ev = st.dataframe(
            display, use_container_width=True, hide_index=True, height=320,
            on_select="rerun", selection_mode="single-row", key="reference_table",
        )
        sel = []
        try:
            sel = list(ev.selection.rows)
        except Exception:
            try:
                sel = list(ev["selection"]["rows"])
            except Exception:
                sel = []
        if sel and 0 <= sel[0] < len(shown):
            picked = shown[sel[0]]["_id"]
    else:
        st.dataframe(display, use_container_width=True, hide_index=True, height=320)
        labels = {
            r["_id"]: f'{r["Username"]} · {r["DOI"]} · {r["Paper Title"][:60]}' for r in shown
        }
        picked = st.selectbox(
            "Select a submission to preview",
            options=[r["_id"] for r in shown],
            format_func=lambda sid: labels.get(sid, sid),
            key="reference_pick",
        )

    if not picked:
        return

    try:
        record = get_submission(picked)
    except Exception as e:
        st.error(f"Could not load it: {e}")
        return
    if not record:
        st.warning("That submission could not be loaded.")
        return

    prompts = (record.get("comparison") or {}).get("prompts") or []
    row = next((r for r in shown if r["_id"] == picked), {})
    title = row.get("Paper Title") or "(title unknown)"
    st.markdown(f"**{title}**")
    st.caption(
        f"Read-only · DOI `{record.get('trial_id','')}` · "
        f"by **{record.get('username','')}** · version `{record.get('version','')}`"
    )
    _render_reference_questions(prompts)

    if st.button(
        "📋 Copy these questions into my form",
        key="reference_copy",
        help="Replaces the questions currently in your form (your DOI and "
             "username are kept).",
    ):
        _populate_form(prompts)
        st.session_state.last_result = {
            "kind": "info",
            "msg": f"Copied {len(prompts)} question(s) into your form as a starting "
            "point. Edit them, then Submit to save your own version.",
        }
        # The questions editor is a separate fragment — rerun the whole app so it
        # picks up the copied questions.
        st.rerun(scope="app")


# ------------- import questions from a JSON file --------------------------

@fragment
def render_import_json() -> None:
    """Let people who wrote questions outside the intake system upload them as
    JSON: validate, preview, then load into the form and save as the first draft."""
    if not st.toggle(
        "📤 Import questions from a JSON file",
        key="show_import_json",
        help="Already wrote your questions elsewhere? Upload them here.",
    ):
        return

    tc1, tc2 = st.columns([1, 2])
    with tc1:
        st.download_button(
            "⬇️ Download JSON template",
            data=json.dumps(example_import_json(), indent=2, ensure_ascii=False),
            file_name="tdb_questions_template.json",
            mime="application/json",
            use_container_width=True,
        )
    with tc2:
        st.caption(
            "Fill the template (or export from your own tool) and upload it below. "
            "Accepted: a list of questions, `{\"prompts\": [...]}`, or a saved record."
        )

    with st.expander("Field reference"):
        st.markdown(
            f"""
| Field | Required | Values |
|---|---|---|
| `question` | yes | free text |
| `question_type` | yes | `{'` / `'.join(QUESTION_TYPES)}` |
| `design_element` | yes | `{'` / `'.join(DESIGN_ELEMENTS)}` — any other text is imported as **Others** |
| `design_element_other` | if Others | free text |
| `id` | no | e.g. `P-001`; assigned if missing, must be unique |
| `rubrics` | no | per dimension: `artifact`, `dimension`, `criteria[]`; generated empty if omitted |
| `criteria[].criterion` | — | free text (empty rows are dropped on save) |
| `criteria[].importance` | no | `{'` / `'.join(IMPORTANCE_OPTIONS)}` (default Medium) |
| `criteria[].scoring` | no | `{'` / `'.join(SCORING_OPTIONS)}` (default Add) |

Dimensions per type — `extraction_only`: `output.json` (no dimension name);
`derivation_required`: `output.json` × Inputs used / Calculated value / Method.
Enum values are matched case-insensitively.
"""
        )

    up = st.file_uploader(
        "Questions JSON", type=["json"],
        key=f"import_json_file_{st.session_state.import_uploader_nonce}",
    )
    if up is None:
        return

    try:
        data = json.loads(up.getvalue().decode("utf-8"))
    except Exception as e:
        st.error(f"Not valid JSON: {e}")
        return

    prompts, errors, warnings = validate_and_normalize_prompts(data)

    if errors:
        st.error(f"❌ {len(errors)} problem(s) — fix these and upload again:")
        for e in errors:
            st.markdown(f"- `{e}`")
        return
    if warnings:
        st.warning(f"⚠️ Imported with {len(warnings)} adjustment(s):")
        for w in warnings:
            st.markdown(f"- `{w}`")
    st.success(
        f"✅ Valid — {len(prompts)} question(s), converted to the form layout. Preview:"
    )
    with st.expander("Preview (read-only)", expanded=False):
        _render_reference_questions(prompts)

    trial_id = normalize_doi(st.session_state.get("trial_id", ""))
    username = st.session_state.get("username", "").strip()
    can_save = bool(trial_id and username)
    if can_save:
        st.caption(
            "Loading also saves these questions as your **first draft** under "
            f"`{trial_id}` / `{username}`."
        )
    else:
        st.caption(
            "Enter **DOI** and **Username** above if you also want this saved as your "
            "first draft — otherwise it is only loaded into the form."
        )

    if st.button(
        "📥 Load into the form — edit below, then Save draft / Submit",
        key="import_json_go",
        type="primary",
        help="Replaces the questions currently in the form with these.",
    ):
        _populate_form(prompts)
        n = len(prompts)
        if can_save:
            try:
                res = save_draft(
                    trial_id, username,
                    {"trial_id": trial_id, "username": username, "prompts": prompts},
                )
                st.session_state.last_result = {
                    "kind": "success",
                    "msg": f"📥 Imported {n} question(s) from `{up.name}` into the form "
                    f"and saved them as draft `{res['version']}`. Edit them below, "
                    "then **Submit** (or **Save draft** again).",
                    "url": res.get("url"),
                }
            except Exception as e:
                st.session_state.last_result = {
                    "kind": "error",
                    "msg": f"Imported {n} question(s) into the form, but saving the "
                    f"draft failed: {e}",
                }
        else:
            st.session_state.last_result = {
                "kind": "success",
                "msg": f"📥 Imported {n} question(s) from `{up.name}` into the form. "
                "Edit them below; enter DOI + Username, then **Save draft** or **Submit**.",
            }
        # Collapse this panel on the next run so the loaded form is right here.
        st.session_state["_collapse_import_panel"] = True
        # The questions editor is a separate fragment — rerun the whole app.
        st.rerun(scope="app")


# ------------- form ------------------------------------------------------

def _render_result_banner(where: str) -> None:
    """Show `last_result` at the place that matches the action that produced it.

    Results tagged ``"at": "bottom"`` (Save draft / Submit) appear under those
    buttons; everything else (load version/draft, copy, import) appears at the
    top of the Questions section, right where the loaded questions start.
    """
    res = st.session_state.last_result
    if not res or (res.get("at") or "top") != where:
        return
    if res["kind"] == "success":
        st.success(res["msg"])
    elif res["kind"] == "error":
        st.error(res["msg"])
    else:
        st.info(res["msg"])
    if res.get("url"):
        st.markdown(f"[View on Hugging Face]({res['url']})")


@fragment
def _questions_fragment() -> None:
    """The questions editor + actions. Runs as a fragment so frequent edits
    here don't trigger a full-app rerun (which would re-send the PDF)."""
    st.subheader("Questions")
    # Results of actions that live *above* this section (load version/draft,
    # copy from a reference, JSON import) are shown here, where their effect is.
    _render_result_banner(where="top")
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

            # text_area (not text_input) so a long question wraps and is fully
            # visible instead of scrolling inside one line.
            st.text_area(
                "Question",
                key=kq(uid, "question"),
                placeholder="e.g., Alpha allocated to PFS",
                height=textarea_height(st.session_state.get(kq(uid, "question"), "")),
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
                                height=textarea_height(
                                    st.session_state.get(kc(uid, j, cid, "criterion"), ""),
                                    min_h=70,
                                ),
                            )
                            cc1, cc2, cc3 = st.columns([2, 2, 1])
                            with cc1:
                                st.selectbox(
                                    "Importance",
                                    options=IMPORTANCE_OPTIONS,
                                    key=kc(uid, j, cid, "importance"),
                                )
                            with cc2:
                                st.selectbox(
                                    "Scoring direction",
                                    options=SCORING_OPTIONS,
                                    key=kc(uid, j, cid, "scoring"),
                                    help="Add: meeting this criterion adds points. "
                                    "Deduct: it deducts points.",
                                )
                            with cc3:
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

    # status banner for the Save draft / Submit buttons just above
    _render_result_banner(where="bottom")

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
            help="Document id (DOI folder) — used to load the SAP/protocol PDF. "
                 "You can paste `10.1056/NEJMoa2511478` or the doi.org link; it is "
                 "normalized to `10.1056_nejmoa2511478`.",
            on_change=_normalize_doi_input,
        )
    with c2:
        st.text_input("Username", key="username", placeholder="e.g., jdoe")

    render_trial_browser()

    fv1, fv2 = st.columns(2)
    with fv1:
        st.button(
            "Find versions",
            on_click=_find_versions,
            use_container_width=True,
            help="List all previously submitted versions for this DOI + username.",
        )
    with fv2:
        st.button(
            "Load draft",
            on_click=_load_draft,
            use_container_width=True,
            help="Load the saved draft for this DOI + username.",
        )

    # Reference document PDF links (directly under Find versions).
    render_pdf_panel()

    # Browse other people's submitted forms as a reference.
    render_reference_browser()

    # Import questions written outside the intake system.
    render_import_json()

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
st.markdown(
    f'🎬 <a href="{DEMO_VIDEO_URL}" target="_blank" rel="noopener">'
    "Watch the demo video ↗</a> — how to fill in this form."
    "&nbsp; · &nbsp;"
    f'📄 <a href="{SYSTEM_PROMPT_URL}" target="_blank" rel="noopener">'
    "System prompt on GitHub ↗</a>",
    unsafe_allow_html=True,
)
with st.expander("📄 System prompt — what the model is asked to do"):
    st.caption(
        "This is the exact prompt used to run models on a submission "
        "(the live copy from `lib/agent.py`)."
    )
    st.code(SYSTEM_PROMPT, language="text")
    st.download_button(
        "Download system_prompt.txt",
        data=SYSTEM_PROMPT,
        file_name="system_prompt.txt",
        mime="text/plain",
    )
if not hf_configured:
    st.info(
        "ℹ️ HF env vars not set — submissions will be written to `./data/submissions/` "
        "(local dev mode)."
    )

render_form()
