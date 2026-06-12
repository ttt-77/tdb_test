"""Constants and helpers for the intake form schema."""

import hashlib
import json
from typing import Literal, TypedDict, List

DESIGN_ELEMENTS: List[str] = [
    "Hypotheses/Endpoints",
    "Multiplicity control",
    "Sample size and power",
    "Interim analyses",
    "Others",
]

QUESTION_TYPES: List[str] = [
    "extraction_only",
    "derivation_required",
]

QuestionType = Literal["extraction_only", "derivation_required", ""]
Status = Literal["pending", "reviewed", "needs_fix"]

VALID_STATUSES: List[str] = ["pending", "reviewed", "needs_fix"]

# Each criterion's importance (replaces the old numeric "points").
IMPORTANCE_OPTIONS: List[str] = ["HIGH", "medium", "low"]


class Criterion(TypedDict):
    criterion: str
    importance: str
    tolerance: str


class Rubric(TypedDict):
    # A dimension block; holds one or more criteria.
    artifact: str
    dimension: str
    criteria: List[Criterion]


class Question(TypedDict):
    id: str
    design_element: str
    design_element_other: str
    question: str
    question_type: str
    rubrics: List[Rubric]


def dimensions_for_type(qt: str):
    """Fixed (artifact, dimension) blocks for a question type, each with the
    number of criterion rows to show by default. The user fills in one or more
    criteria under each; the first is primary, extras are optional."""
    if qt == "extraction_only":
        return [{"artifact": "output.json", "dimension": "", "default_criteria": 1}]
    if qt == "derivation_required":
        return [
            {"artifact": "output.json", "dimension": "Inputs used", "default_criteria": 1},
            {"artifact": "output.json", "dimension": "Calculated value", "default_criteria": 1},
            {"artifact": "output.json", "dimension": "Method", "default_criteria": 3},
        ]
    return []


def blank_question(qid: str) -> Question:
    return {
        "id": qid,
        "design_element": "",
        "design_element_other": "",
        "question": "",
        "question_type": "",
        "rubrics": [],
    }


def next_question_id(existing: List[Question]) -> str:
    nums = []
    for q in existing:
        qid = q.get("id", "")
        if qid.startswith("P-"):
            try:
                nums.append(int(qid[2:]))
            except ValueError:
                pass
    return f"P-{(max(nums) + 1 if nums else 1):03d}"


def question_content_hash(q: dict) -> str:
    """Stable hash of a question's *content* (excludes its id).

    Used to detect whether a question was edited since it was reviewed: if the
    current content hash differs from the hash stored on a review, that review
    no longer applies to the current content.
    """
    canonical = {
        "design_element": q.get("design_element", ""),
        "design_element_other": q.get("design_element_other", ""),
        "question": q.get("question", ""),
        "question_type": q.get("question_type", ""),
        "rubrics": [
            {
                "artifact": r.get("artifact", ""),
                "dimension": r.get("dimension", ""),
                "criteria": [
                    {
                        "criterion": c.get("criterion", ""),
                        "importance": c.get("importance", ""),
                        "tolerance": c.get("tolerance", ""),
                    }
                    for c in (r.get("criteria") or [])
                ],
            }
            for r in (q.get("rubrics") or [])
        ],
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()
