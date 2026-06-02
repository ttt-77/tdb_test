export type TrialDesign = {
  trialName: string;
  nctId: string;
  therapeuticArea: string;
  phase: string;
  indication: string;
  designType: string;
  randomizationRatio: string;
  blinding: string;
  arms: string;
  primaryEndpoint: string;
  secondaryEndpoints: string;
  sampleSize: string;
  primaryAnalysisMethod: string;
  populationAnalysisSet: string;
  multiplicityAdjustment: string;
  interimAnalyses: string;
  missingDataHandling: string;
  notes: string;
};

export type PreAssessment = {
  yearsExperience: string;
  primarySetting:
    | "industry"
    | "academia"
    | "cro"
    | "regulator"
    | "hospital"
    | "other"
    | "";
  therapeuticAreas: string[];
  therapeuticAreasOther: string;
  aiToolsUsed: string[];
  aiToolsUsedOther: string;
  aiTasks: string[];
  aiTasksOther: string;
  aiUsageFrequency: "never" | "rarely" | "monthly" | "weekly" | "daily" | "";
  expectedAccuracy: "gt90" | "70to90" | "50to70" | "lt50" | "";
  hardestParts: string;
  reasoning: string;
};

export type FieldAccuracy = "correct" | "partial" | "incorrect" | "missing" | "";

export type QuestionType = "extraction_only" | "derivation_required" | "";

export type Rubric = {
  artifact: string;
  dimension: string;
  points: string;
  criterion: string;
  tolerance: string;
};

export type PromptItem = {
  id: string;
  design_element: string;
  design_element_other: string;
  question: string;
  question_type: QuestionType;
  rubrics: Rubric[];
};

export const DESIGN_ELEMENT_OPTIONS = [
  "Hypotheses/Endpoints",
  "Multiplicity control",
  "Sample size and power",
  "Interim analyses",
  "Others",
] as const;

export const QUESTION_TYPE_OPTIONS: { v: QuestionType; label: string }[] = [
  { v: "extraction_only", label: "Extraction only" },
  { v: "derivation_required", label: "Derivation required" },
];

export const blankRubric = (artifact = "", dimension = ""): Rubric => ({
  artifact,
  dimension,
  points: "",
  criterion: "",
  tolerance: "",
});

export function rubricsForType(type: QuestionType): Rubric[] {
  if (type === "extraction_only") {
    return [blankRubric("output.json", "")];
  }
  if (type === "derivation_required") {
    return [
      blankRubric("output.json", "Inputs used"),
      blankRubric("output.json", "Calculated value"),
      blankRubric("output.json", "Method"),
      blankRubric("output.R", "Reproducibility"),
    ];
  }
  return [];
}

export type Comparison = {
  trial_id: string;
  username: string;
  prompts: PromptItem[];
};

export type AiPromptOutput = {
  id: string;
  design_element?: string;
  question?: string;
  question_type?: QuestionType;
  output?: {
    extracted_value?: string | null;
    dimensions?: {
      inputs_used?: string | null;
      method?: string | null;
      calculated_value?: string | null;
    };
  };
};

export type AiOutputFile = { prompts?: AiPromptOutput[] } & Partial<TrialDesign>;

// Agent output (post-run) — public/agent-outputs/<session>.json
export type AgentOutputItem = {
  id: string;
  design_element: string;
  question: string;
  question_type: QuestionType;
  output: {
    extracted_value?: string | null;
    dimensions?: {
      inputs_used?: string | null;
      method?: string | null;
      calculated_value?: string | null;
    };
  };
};

export type AgentOutputFile = { output: AgentOutputItem[] };


export type CoAuthor = {
  name: string;
  title: string;
  affiliation: string;
  email: string;
  orcid: string;
  interestedFollowUp: "yes" | "maybe" | "no" | "";
  willingCoAuthor: "yes" | "maybe" | "no" | "";
  contributionAreas: string[];
  hoursPerMonth: string;
  comments: string;
};

export type Submission = {
  submittedAt: string;
  sessionId: string;
  design: TrialDesign;
  preAssessment: PreAssessment;
  comparison: Comparison;
  coAuthor: CoAuthor;
};

export const emptyDesign: TrialDesign = {
  trialName: "",
  nctId: "",
  therapeuticArea: "",
  phase: "",
  indication: "",
  designType: "",
  randomizationRatio: "",
  blinding: "",
  arms: "",
  primaryEndpoint: "",
  secondaryEndpoints: "",
  sampleSize: "",
  primaryAnalysisMethod: "",
  populationAnalysisSet: "",
  multiplicityAdjustment: "",
  interimAnalyses: "",
  missingDataHandling: "",
  notes: "",
};

export const designFieldLabels: Record<keyof TrialDesign, string> = {
  trialName: "Trial name / short title",
  nctId: "NCT ID (if any)",
  therapeuticArea: "Therapeutic area",
  phase: "Phase",
  indication: "Indication / population",
  designType: "Design type (parallel, crossover, adaptive, platform, etc.)",
  randomizationRatio: "Randomization ratio",
  blinding: "Blinding",
  arms: "Treatment arms",
  primaryEndpoint: "Primary endpoint",
  secondaryEndpoints: "Key secondary endpoints",
  sampleSize: "Sample size & power assumptions",
  primaryAnalysisMethod: "Primary analysis method / model",
  populationAnalysisSet: "Analysis population (ITT, PP, mITT, etc.)",
  multiplicityAdjustment: "Multiplicity adjustment",
  interimAnalyses: "Interim analyses / stopping rules",
  missingDataHandling: "Missing data handling",
  notes: "Other design notes",
};
