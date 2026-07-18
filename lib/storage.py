"""Storage backend: Hugging Face Dataset repo, with local filesystem fallback for dev.

Layout inside the dataset repo:

    submissions/<trial>__<user>__<stamp>.json          (immutable: the submission)
    reviews/<trial>__<user>__<stamp>/<stamp>__<rev>.json (one file per review)

Submissions are never rewritten. Each review is a brand-new file, so multiple
reviewers can review the same submission concurrently with no write conflict.
The "current status" of a submission is derived from its most recent review.

Env vars (set in HF Space → Settings → Variables and secrets):
    HF_TOKEN            - HF user access token with Write permission
    HF_DATASET_REPO     - e.g. "ttt-77/tdb-intake-submissions"
    HF_DATASET_BRANCH   - optional, defaults to "main"
    ADMIN_PASSWORD      - shared password for the Admin page

If HF_TOKEN or HF_DATASET_REPO is missing, all I/O goes to ./data/... so local
dev works without HF credentials.
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

from lib.schema import question_content_hash

HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
HF_DATASET_REPO = os.environ.get("HF_DATASET_REPO", "").strip()
HF_DATASET_BRANCH = os.environ.get("HF_DATASET_BRANCH", "main").strip() or "main"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

hf_configured = bool(HF_TOKEN and HF_DATASET_REPO)

_api = HfApi(token=HF_TOKEN) if hf_configured else None

LOCAL_DATA_DIR = Path("data")
SUBMISSIONS_PREFIX = "submissions"
REVIEWS_PREFIX = "reviews"


def _safe(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", (s or "").strip())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp(iso: Optional[str] = None) -> str:
    return (iso or _now_iso()).replace(":", "-").replace(".", "-")


def _pair_dir(trial_id: str, username: str) -> str:
    """Folder holding all versions for a (trial_id, username) pair."""
    return f"{SUBMISSIONS_PREFIX}/{_safe(trial_id)}__{_safe(username)}"


def _base_id(submission_id: str) -> str:
    """Path of a submission relative to the submissions/ prefix, without .json.

    'submissions/NCT99__jdoe/2026-...json' -> 'NCT99__jdoe/2026-...'
    Used to key the matching reviews/ folder so reviews stay grouped per
    (pair, version).
    """
    s = submission_id
    if s.startswith(f"{SUBMISSIONS_PREFIX}/"):
        s = s[len(SUBMISSIONS_PREFIX) + 1 :]
    if s.endswith(".json"):
        s = s[:-5]
    return s




# ---- low-level read/write/list (HF or local) -----------------------------

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
    url = (
        f"https://huggingface.co/datasets/{HF_DATASET_REPO}"
        f"/resolve/{HF_DATASET_BRANCH}/{path_in_repo}"
    )
    r = requests.get(url, headers={"Authorization": f"Bearer {HF_TOKEN}"}, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _write_json(path_in_repo: str, payload: Dict[str, Any], commit_message: str) -> None:
    if hf_configured:
        _hf_upload_json(path_in_repo, payload, commit_message)
        return
    p = LOCAL_DATA_DIR / path_in_repo
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path_in_repo: str) -> Optional[Dict[str, Any]]:
    if hf_configured:
        return _hf_read_json(path_in_repo)
    p = LOCAL_DATA_DIR / path_in_repo
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _all_files() -> List[str]:
    """List every file path in the repo (HF) or under ./data (local)."""
    if hf_configured:
        assert _api is not None
        try:
            return _api.list_repo_files(
                repo_id=HF_DATASET_REPO,
                repo_type="dataset",
                revision=HF_DATASET_BRANCH,
            )
        except HfHubHTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return []
            raise
    if not LOCAL_DATA_DIR.exists():
        return []
    return [p.relative_to(LOCAL_DATA_DIR).as_posix() for p in LOCAL_DATA_DIR.rglob("*.json")]


# ---- public API ----------------------------------------------------------

def save_submission(trial_id: str, username: str, comparison: Dict[str, Any]) -> Dict[str, Any]:
    """Save a NEW version for (trial_id, username).

    Every submit creates a new version file under
    submissions/<trial>__<user>/<stamp>.json — nothing is overwritten, so the
    full version history is kept and any version can be loaded back.
    """
    now = _now_iso()
    version = _stamp(now)
    submission_id = f"{_pair_dir(trial_id, username)}/{version}.json"
    record = {
        "submissionId": submission_id,
        "version": version,
        "submittedAt": now,
        "trial_id": trial_id,
        "username": username,
        "comparison": comparison,
    }
    _write_json(
        submission_id,
        record,
        f"Add submission: {trial_id} — {username} ({version})",
    )
    url = (
        f"https://huggingface.co/datasets/{HF_DATASET_REPO}"
        f"/blob/{HF_DATASET_BRANCH}/{submission_id}"
        if hf_configured
        else None
    )
    return {"submissionId": submission_id, "url": url, "record": record, "version": version}


def _drafts_dir(trial_id: str, username: str) -> str:
    return f"{_pair_dir(trial_id, username)}/drafts"


def save_draft(trial_id: str, username: str, comparison: Dict[str, Any]) -> Dict[str, Any]:
    """Save a timestamped draft for (trial_id, username) under
    submissions/<trial>__<user>/drafts/<stamp>.json. Each save keeps history;
    drafts are not versions and are excluded from version listings."""
    now = _now_iso()
    stamp = _stamp(now)
    path = f"{_drafts_dir(trial_id, username)}/{stamp}.json"
    record = {
        "savedAt": now,
        "version": stamp,
        "trial_id": trial_id,
        "username": username,
        "comparison": comparison,
    }
    _write_json(path, record, f"Save draft: {trial_id} — {username} ({stamp})")
    url = (
        f"https://huggingface.co/datasets/{HF_DATASET_REPO}"
        f"/blob/{HF_DATASET_BRANCH}/{path}"
        if hf_configured
        else None
    )
    return {"path": path, "url": url, "savedAt": now, "version": stamp}


def get_draft(
    trial_id: str, username: str, all_files: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """Load the most recent draft for (trial_id, username), or None."""
    prefix = f"{_drafts_dir(trial_id, username)}/"
    files = all_files if all_files is not None else _all_files()
    paths = sorted(f for f in files if f.startswith(prefix) and f.endswith(".json"))
    if not paths:
        return None
    return _read_json(paths[-1])  # max stamp = latest draft


def list_versions(
    trial_id: str, username: str, all_files: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """All saved versions for (trial_id, username), newest first.

    Each item: submissionId, version, submittedAt, num_questions, status,
    review_count, reviews (full timeline for that version).
    """
    prefix = f"{_pair_dir(trial_id, username)}/"
    files = all_files if all_files is not None else _all_files()
    paths = sorted(
        (
            f
            for f in files
            if f.startswith(prefix) and f.endswith(".json") and "/drafts/" not in f
        ),
        reverse=True,
    )
    out: List[Dict[str, Any]] = []
    for p in paths:
        rec = _read_json(p)
        if not rec:
            continue
        prompts = (rec.get("comparison") or {}).get("prompts") or []
        reviews = list_reviews(p, all_files=files)
        latest = reviews[-1] if reviews else None
        out.append(
            {
                "submissionId": p,
                "version": rec.get("version", ""),
                "submittedAt": rec.get("submittedAt", ""),
                "num_questions": len(prompts),
                "status": latest["status"] if latest else "pending",
                "review_count": len(reviews),
                "reviews": reviews,
            }
        )
    return out


def add_review(
    submission_id: str,
    status: str,
    reviewer: str,
    note: str = "",
    question_id: str = "",
) -> Dict[str, Any]:
    """Append a review as its own file under reviews/<base>/.

    Each review is a new file (never overwrites), so concurrent reviews by
    different people cannot conflict. If ``question_id`` is given, the review
    targets that specific question; otherwise it is an overall (whole-version)
    review.
    """
    base = _base_id(submission_id)
    now = _now_iso()
    review = {
        "submissionId": submission_id,
        "at": now,
        "reviewer": reviewer,
        "status": status,
        "note": note,
        "question_id": question_id,
    }
    # Snapshot the reviewed question's content so we can later tell if it was
    # edited (which invalidates this review).
    if question_id:
        sub = get_submission(submission_id)
        prompts = (sub.get("comparison") or {}).get("prompts") or [] if sub else []
        q = next((x for x in prompts if x.get("id") == question_id), None)
        if q is not None:
            review["question_hash"] = question_content_hash(q)
    qtag = _safe(question_id) if question_id else "all"
    review_path = (
        f"{REVIEWS_PREFIX}/{base}/{_stamp(now)}__{_safe(reviewer) or 'anon'}__{qtag}.json"
    )
    target = f"Q {question_id}" if question_id else "overall"
    _write_json(
        review_path,
        review,
        f"Review ({status}, {target}) by {reviewer or 'anon'} on {base}",
    )
    return review


def list_pair_reviews(
    pair_key: str, all_files: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Every review across ALL versions of a (trial_id, username) pair.

    pair_key is '<trial>__<user>'. Each returned review is tagged with the
    `version` it was made on. Oldest first.
    """
    prefix = f"{REVIEWS_PREFIX}/{pair_key}/"
    files = all_files if all_files is not None else _all_files()
    paths = sorted(f for f in files if f.startswith(prefix) and f.endswith(".json"))
    out: List[Dict[str, Any]] = []
    for p in paths:
        rec = _read_json(p)
        if not rec:
            continue
        # p = reviews/<pair>/<version>/<revfile>.json
        parts = p[len(REVIEWS_PREFIX) + 1 :].split("/")
        rec = dict(rec)
        rec["version"] = parts[1] if len(parts) >= 3 else ""
        out.append(rec)
    out.sort(key=lambda r: r.get("at", ""))
    return out


def pair_reviews(
    trial_id: str, username: str, all_files: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """All reviews across all versions for a (trial_id, username), oldest first.

    Convenience wrapper around list_pair_reviews for callers that have the raw
    trial_id / username (e.g. the public form).
    """
    pair_key = f"{_safe(trial_id)}__{_safe(username)}"
    return list_pair_reviews(pair_key, all_files=all_files)


def list_reviews(submission_id: str, all_files: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """All reviews for a single submission version, oldest first."""
    base = _base_id(submission_id)
    prefix = f"{REVIEWS_PREFIX}/{base}/"
    files = all_files if all_files is not None else _all_files()
    paths = sorted(f for f in files if f.startswith(prefix) and f.endswith(".json"))
    reviews = [r for r in (_read_json(p) for p in paths) if r]
    reviews.sort(key=lambda r: r.get("at", ""))
    return reviews


def get_submission(submission_id: str) -> Optional[Dict[str, Any]]:
    if not submission_id.startswith(f"{SUBMISSIONS_PREFIX}/"):
        return None
    return _read_json(submission_id)


def _pair_key_from_path(p: str) -> str:
    """Group key for a submission path = its (trial_id, username) folder.

    'submissions/NCT99__jdoe/2026-...json' -> 'NCT99__jdoe'
    'submissions/foo.json' (legacy flat) -> 'foo'
    """
    rest = p[len(SUBMISSIONS_PREFIX) + 1 :]
    if "/" in rest:
        return rest.rsplit("/", 1)[0]
    return rest[:-5] if rest.endswith(".json") else rest


def list_submissions() -> List[Dict[str, Any]]:
    """Latest version of each (trial_id, username), with its review timeline.

    Reviewers see one row per trial — only the newest version. Each item:
    submissionId, trial_id, username, version, submittedAt, status, reviewedAt,
    reviewer, review_count, reviews (list), submission (full record).
    """
    files = _all_files()
    sub_paths = [
        f
        for f in files
        if f.startswith(f"{SUBMISSIONS_PREFIX}/")
        and f.endswith(".json")
        and "/drafts/" not in f
    ]
    # Keep only the newest version path per pair (stamps sort lexically).
    latest_by_pair: Dict[str, str] = {}
    for sp in sub_paths:
        key = _pair_key_from_path(sp)
        if key not in latest_by_pair or sp > latest_by_pair[key]:
            latest_by_pair[key] = sp

    result: List[Dict[str, Any]] = []
    for key, sp in latest_by_pair.items():
        sub = _read_json(sp)
        if not sub:
            continue
        # Overall reviews (not tied to a question) on the latest version drive
        # the current status.
        reviews = list_reviews(sp, all_files=files)
        overall = [r for r in reviews if not r.get("question_id")]
        latest = overall[-1] if overall else None
        # All reviews across every version of this trial (tagged with version).
        all_reviews = list_pair_reviews(key, all_files=files)
        result.append(
            {
                "submissionId": sp,
                "trial_id": sub.get("trial_id", ""),
                "username": sub.get("username", ""),
                "version": sub.get("version", ""),
                "submittedAt": sub.get("submittedAt", ""),
                "status": latest["status"] if latest else "pending",
                "reviewedAt": latest["at"] if latest else "",
                "reviewer": latest["reviewer"] if latest else "",
                "review_count": len(reviews),
                "reviews": reviews,
                "all_reviews": all_reviews,
                "submission": sub,
            }
        )
    result.sort(key=lambda r: r.get("submittedAt", ""), reverse=True)
    return result


# ---- admin gate ----------------------------------------------------------

def check_admin_password(supplied: str) -> bool:
    if not ADMIN_PASSWORD:
        return True  # no password configured = open (dev mode)
    return supplied == ADMIN_PASSWORD
