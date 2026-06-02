"""Storage backend: Hugging Face Dataset repo, with local filesystem fallback for dev.

Env vars (set in HF Space → Settings → Variables and secrets):
    HF_TOKEN            - HF user access token with Write permission
    HF_DATASET_REPO     - e.g. "ttt-77/tdb-intake-submissions"
    HF_DATASET_BRANCH   - optional, defaults to "main"
    ADMIN_PASSWORD      - shared password for the /Admin page

If HF_TOKEN or HF_DATASET_REPO is missing, all I/O goes to ./data/submissions/*.json
so local dev works without HF credentials.
"""

from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError

HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
HF_DATASET_REPO = os.environ.get("HF_DATASET_REPO", "").strip()
HF_DATASET_BRANCH = os.environ.get("HF_DATASET_BRANCH", "main").strip() or "main"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

hf_configured = bool(HF_TOKEN and HF_DATASET_REPO)

_api = HfApi(token=HF_TOKEN) if hf_configured else None

LOCAL_DATA_DIR = Path("data")
SUBMISSIONS_PREFIX = "submissions"


def _safe(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", (s or "").strip())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp_for_filename() -> str:
    return _now_iso().replace(":", "-").replace(".", "-")


# ---- HF helpers ----------------------------------------------------------

def _hf_upload_json(path_in_repo: str, payload: Dict[str, Any], commit_message: str) -> None:
    assert _api is not None
    content = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    _api.upload_file(
        path_or_fileobj=io.BytesIO(content),
        path_in_repo=path_in_repo,
        repo_id=HF_DATASET_REPO,
        repo_type="dataset",
        revision=HF_DATASET_BRANCH,
        commit_message=commit_message,
    )


def _hf_read_json(path_in_repo: str) -> Optional[Dict[str, Any]]:
    """Fetch via the resolve URL — no cache, always fresh."""
    url = (
        f"https://huggingface.co/datasets/{HF_DATASET_REPO}"
        f"/resolve/{HF_DATASET_BRANCH}/{path_in_repo}"
    )
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _hf_list_submissions() -> List[str]:
    assert _api is not None
    try:
        files = _api.list_repo_files(
            repo_id=HF_DATASET_REPO,
            repo_type="dataset",
            revision=HF_DATASET_BRANCH,
        )
    except HfHubHTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise
    return [
        f for f in files
        if f.startswith(f"{SUBMISSIONS_PREFIX}/") and f.endswith(".json")
    ]


# ---- Public API ----------------------------------------------------------

def create_submission(trial_id: str, username: str, comparison: Dict[str, Any]) -> Dict[str, Any]:
    """Write a new submission. Returns a result dict with submissionId and (optionally) url."""
    file_name = f"{_safe(trial_id)}__{_safe(username)}__{_stamp_for_filename()}.json"
    submission_id = f"{SUBMISSIONS_PREFIX}/{file_name}"
    record = {
        "submissionId": submission_id,
        "submittedAt": _now_iso(),
        "trial_id": trial_id,
        "username": username,
        # These top-level fields mirror the most recent review for easy
        # filtering/sorting; they are updated by add_review().
        "status": "pending",
        "reviewer": "",
        "reviewerNote": "",
        "reviewedAt": "",
        # Full append-only log: one entry per review by any reviewer.
        "review_history": [],
        "comparison": comparison,
    }

    if hf_configured:
        _hf_upload_json(
            submission_id,
            record,
            commit_message=f"Add submission: {trial_id} — {username}",
        )
        return {
            "submissionId": submission_id,
            "url": (
                f"https://huggingface.co/datasets/{HF_DATASET_REPO}"
                f"/blob/{HF_DATASET_BRANCH}/{submission_id}"
            ),
            "record": record,
        }

    # local fs fallback
    path = LOCAL_DATA_DIR / submission_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"submissionId": submission_id, "url": None, "record": record}


def list_submissions() -> List[Dict[str, Any]]:
    """Return summaries (small fields only) of every submission."""
    if hf_configured:
        records = []
        for path in _hf_list_submissions():
            data = _hf_read_json(path)
            if not data:
                continue
            records.append(_summarize(data))
        records.sort(key=lambda r: r.get("submittedAt", ""), reverse=True)
        return records

    dir_ = LOCAL_DATA_DIR / SUBMISSIONS_PREFIX
    if not dir_.exists():
        return []
    summaries = []
    for f in sorted(dir_.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            summaries.append(_summarize(data))
        except Exception:
            continue
    return summaries


def get_submission(submission_id: str) -> Optional[Dict[str, Any]]:
    if not submission_id.startswith(f"{SUBMISSIONS_PREFIX}/"):
        return None
    if hf_configured:
        return _hf_read_json(submission_id)
    path = LOCAL_DATA_DIR / submission_id
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def add_review(
    submission_id: str,
    status: str,
    reviewer: str,
    note: str = "",
) -> Optional[Dict[str, Any]]:
    """Append a review to the submission's history.

    A submission can be reviewed many times by different people. Each call adds
    one entry to ``review_history`` and mirrors it into the top-level
    status/reviewer/reviewerNote/reviewedAt fields (which always reflect the
    latest review).
    """
    record = get_submission(submission_id)
    if record is None:
        return None

    now = _now_iso()
    entry = {
        "at": now,
        "reviewer": reviewer,
        "status": status,
        "note": note,
    }
    history = record.get("review_history")
    if not isinstance(history, list):
        history = []
    history.append(entry)
    record["review_history"] = history

    # Mirror the latest review into the top-level fields.
    record["status"] = status
    record["reviewer"] = reviewer
    record["reviewerNote"] = note
    record["reviewedAt"] = now

    if hf_configured:
        _hf_upload_json(
            submission_id,
            record,
            commit_message=f"Review ({status}) by {reviewer or 'anon'}: "
            f"{submission_id.rsplit('/', 1)[-1]}",
        )
    else:
        path = LOCAL_DATA_DIR / submission_id
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def _summarize(record: Dict[str, Any]) -> Dict[str, Any]:
    history = record.get("review_history")
    review_count = len(history) if isinstance(history, list) else 0
    return {
        "submissionId": record.get("submissionId", ""),
        "trial_id": record.get("trial_id", ""),
        "username": record.get("username", ""),
        "submittedAt": record.get("submittedAt", ""),
        "status": record.get("status", "pending"),
        "reviewedAt": record.get("reviewedAt", ""),
        "reviewer": record.get("reviewer", ""),
        "review_count": review_count,
    }


# ---- Admin gate ----------------------------------------------------------

def check_admin_password(supplied: str) -> bool:
    if not ADMIN_PASSWORD:
        return True  # no password configured = open (dev mode)
    return supplied == ADMIN_PASSWORD
