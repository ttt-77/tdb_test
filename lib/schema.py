"""Constants and helpers for the intake form schema."""

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


class Rubric(TypedDict):
    artifact: str
    dimension: str
    points: str
    criterion: str
    tolerance: str


class Question(TypedDict):
    id: str
    design_element: str
    design_element_other: str
    question: str
    question_type: str
    rubrics: List[Rubric]


def blank_rubric(artifact: str = "", dimension: str = "") -> Rubric:
    return {
        "artifact": artifact,
        "dimension": dimension,
        "points": "",
        "criterion": "",
        "tolerance": "",
    }


def rubrics_for_type(qt: str) -> List[Rubric]:
    if qt == "extraction_only":
        return [blank_rubric("output.json", "")]
    if qt == "derivation_required":
        return [
            blank_rubric("output.json", "Inputs used"),
            blank_rubric("output.json", "Calculated value"),
            blank_rubric("output.json", "Method"),
            blank_rubric("output.R", "Reproducibility"),
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
