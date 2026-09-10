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
IMPORTANCE_OPTIONS: List[str] = ["High", "Medium", "Low"]

# Whether meeting a criterion adds or deducts points.
SCORING_OPTIONS: List[str] = ["Add", "Deduct"]


class Criterion(TypedDict):
    criterion: str
    importance: str
    scoring: str


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
                        "scoring": c.get("scoring", ""),
                    }
                    for c in (r.get("criteria") or [])
                ],
            }
            for r in (q.get("rubrics") or [])
        ],
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Importing questions written outside the intake system (JSON upload)
# ---------------------------------------------------------------------------

def _ci_match(value: str, options: List[str]) -> str:
    """Case/space-insensitive match of `value` against `options`; '' if none."""
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    for o in options:
        if key == o.lower().replace(" ", "_").replace("-", "_"):
            return o
    return ""


def validate_and_normalize_prompts(data):
    """Validate an uploaded JSON payload and normalize it to the form's schema.

    Accepts a bare list of questions, ``{"prompts": [...]}``, or a full saved
    record (``{"comparison": {"prompts": [...]}}``).

    Returns ``(prompts, errors, warnings)``. If ``errors`` is non-empty the
    payload must not be imported. Normalization fills defaults (missing ids,
    importance/scoring), canonicalizes enum casing, maps an unknown
    design_element onto "Others", and generates the rubric dimension blocks
    when a question has none.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # -- unwrap the accepted shapes -----------------------------------------
    if isinstance(data, dict) and isinstance(data.get("comparison"), dict):
        data = data["comparison"]
    if isinstance(data, dict):
        raw = data.get("prompts")
        if raw is None:
            return [], ['Top-level object must contain a "prompts" list.'], warnings
    else:
        raw = data
    if not isinstance(raw, list):
        return [], ['"prompts" must be a list of questions.'], warnings
    if not raw:
        return [], ["The questions list is empty."], warnings

    prompts: List[dict] = []
    seen_ids: set = set()
    next_num = 1

    for i, q in enumerate(raw):
        loc = f"prompts[{i}]"
        if not isinstance(q, dict):
            errors.append(f"{loc}: must be an object.")
            continue

        # question text
        question = str(q.get("question") or "").strip()
        if not question:
            errors.append(f"{loc}.question: required (non-empty text).")

        # question_type
        qt = _ci_match(q.get("question_type", ""), QUESTION_TYPES)
        if not qt:
            errors.append(
                f"{loc}.question_type: {q.get('question_type')!r} is not one of "
                f"{QUESTION_TYPES}."
            )

        # design_element (unknown value -> Others + design_element_other)
        de_raw = str(q.get("design_element") or "").strip()
        de_other = str(q.get("design_element_other") or "").strip()
        de = _ci_match(de_raw, DESIGN_ELEMENTS)
        if not de and de_raw:
            de, de_other = "Others", de_other or de_raw
            warnings.append(
                f"{loc}.design_element: {de_raw!r} is not a standard option; "
                f"imported as Others: {de_other!r}."
            )
        if not de:
            errors.append(f"{loc}.design_element: required; one of {DESIGN_ELEMENTS}.")
        elif de == "Others" and not de_other:
            errors.append(f"{loc}.design_element_other: required when design_element is Others.")

        # id (assign if missing; must be unique)
        qid = str(q.get("id") or "").strip()
        if not qid:
            while f"P-{next_num:03d}" in seen_ids:
                next_num += 1
            qid = f"P-{next_num:03d}"
            warnings.append(f"{loc}: no id given; assigned {qid}.")
        if qid in seen_ids:
            errors.append(f"{loc}.id: duplicate id {qid!r}.")
        seen_ids.add(qid)
        if qid.startswith("P-"):
            try:
                next_num = max(next_num, int(qid[2:]) + 1)
            except ValueError:
                pass

        # rubrics
        expected = dimensions_for_type(qt) if qt else []
        rubrics_out: List[dict] = []
        rubrics_raw = q.get("rubrics")
        if rubrics_raw is None:
            # Generate the dimension blocks (with the usual number of empty rows).
            for dim in expected:
                rubrics_out.append(
                    {
                        "artifact": dim["artifact"],
                        "dimension": dim["dimension"],
                        "criteria": [
                            {"criterion": "", "importance": "Medium", "scoring": "Add"}
                            for _ in range(max(1, int(dim.get("default_criteria", 1))))
                        ],
                    }
                )
            if expected:
                warnings.append(f"{loc}: no rubrics given; empty dimension blocks generated.")
        elif not isinstance(rubrics_raw, list):
            errors.append(f"{loc}.rubrics: must be a list.")
        else:
            allowed = {(d["artifact"], d["dimension"].lower()): d["dimension"] for d in expected}
            for j, r in enumerate(rubrics_raw):
                rloc = f"{loc}.rubrics[{j}]"
                if not isinstance(r, dict):
                    errors.append(f"{rloc}: must be an object.")
                    continue
                artifact = str(r.get("artifact") or "output.json").strip()
                dim_raw = str(r.get("dimension") or "").strip()
                canon = allowed.get((artifact, dim_raw.lower()))
                if qt and canon is None:
                    exp = ", ".join(
                        f'{d["artifact"]} / "{d["dimension"]}"' for d in expected
                    ) or "(none)"
                    errors.append(
                        f"{rloc}: dimension {dim_raw!r} on {artifact!r} is not valid for "
                        f"{qt}; expected one of: {exp}."
                    )
                    continue
                crits_raw = r.get("criteria")
                if crits_raw is None:
                    # legacy single-criterion rubric
                    # (legacy numeric "points" is not an importance level — ignored)
                    crits_raw = [
                        {
                            "criterion": r.get("criterion", ""),
                            "importance": r.get("importance", ""),
                            "scoring": r.get("scoring", ""),
                        }
                    ]
                if not isinstance(crits_raw, list):
                    errors.append(f"{rloc}.criteria: must be a list.")
                    continue
                crits_out = []
                for k, c in enumerate(crits_raw):
                    cloc = f"{rloc}.criteria[{k}]"
                    if not isinstance(c, dict):
                        errors.append(f"{cloc}: must be an object.")
                        continue
                    imp = _ci_match(c.get("importance", ""), IMPORTANCE_OPTIONS)
                    if c.get("importance") and not imp:
                        errors.append(
                            f"{cloc}.importance: {c.get('importance')!r} is not one of "
                            f"{IMPORTANCE_OPTIONS}."
                        )
                    sco = _ci_match(c.get("scoring", ""), SCORING_OPTIONS)
                    if c.get("scoring") and not sco:
                        errors.append(
                            f"{cloc}.scoring: {c.get('scoring')!r} is not one of "
                            f"{SCORING_OPTIONS}."
                        )
                    crits_out.append(
                        {
                            "criterion": str(c.get("criterion") or "").strip(),
                            "importance": imp or "Medium",
                            "scoring": sco or "Add",
                        }
                    )
                rubrics_out.append(
                    {"artifact": artifact, "dimension": canon or dim_raw, "criteria": crits_out}
                )

        prompts.append(
            {
                "id": qid,
                "design_element": de,
                "design_element_other": de_other if de == "Others" else "",
                "question": question,
                "question_type": qt,
                "rubrics": rubrics_out,
            }
        )

    return prompts, errors, warnings


def example_import_json() -> dict:
    """A small, valid example of the import format (kept in sync with the schema)."""
    return {
        "prompts": [
            {
                "id": "P-001",
                "design_element": "Multiplicity control",
                "question": "What is the total alpha, and how is it initially allocated across the endpoints?",
                "question_type": "extraction_only",
                "rubrics": [
                    {
                        "artifact": "output.json",
                        "dimension": "",
                        "criteria": [
                            {"criterion": "Reports the total two-sided alpha", "importance": "High", "scoring": "Add"},
                            {"criterion": "Cites the SAP section and page", "importance": "Medium", "scoring": "Add"},
                        ],
                    }
                ],
            },
            {
                "id": "P-002",
                "design_element": "Sample size and power",
                "question": "What is the total required number of PFS events at the final analysis?",
                "question_type": "derivation_required",
                "rubrics": [
                    {"artifact": "output.json", "dimension": "Inputs used",
                     "criteria": [{"criterion": "Lists HR, power, alpha and allocation ratio", "importance": "High", "scoring": "Add"}]},
                    {"artifact": "output.json", "dimension": "Calculated value",
                     "criteria": [{"criterion": "Within 2% of the SAP's event count", "importance": "High", "scoring": "Add"}]},
                    {"artifact": "output.json", "dimension": "Method",
                     "criteria": [
                         {"criterion": "Uses the Schoenfeld formula", "importance": "High", "scoring": "Add"},
                         {"criterion": "Relies on an assumption not stated in the SAP", "importance": "Low", "scoring": "Deduct"},
                     ]},
                ],
            },
        ]
    }
