"use client";
import {
  Comparison,
  DESIGN_ELEMENT_OPTIONS,
  PromptItem,
  QUESTION_TYPE_OPTIONS,
  QuestionType,
  Rubric,
  rubricsForType,
} from "@/lib/types";

type Props = {
  value: Comparison;
  onChange: (v: Comparison) => void;
};

const nextPromptId = (existing: PromptItem[]) => {
  const nums = existing
    .map((p) => /^P-(\d+)$/.exec(p.id)?.[1])
    .filter(Boolean)
    .map((n) => parseInt(n as string, 10));
  const max = nums.length ? Math.max(...nums) : 0;
  return `P-${String(max + 1).padStart(3, "0")}`;
};

const blankPrompt = (id: string): PromptItem => ({
  id,
  design_element: "",
  question: "",
  question_type: "",
  rubrics: [],
});

export default function StepCompare({ value, onChange }: Props) {
  const setPrompt = (idx: number, patch: Partial<PromptItem>) => {
    const next = value.prompts.map((p, i) => (i === idx ? { ...p, ...patch } : p));
    onChange({ ...value, prompts: next });
  };

  const setRubric = (pIdx: number, rIdx: number, patch: Partial<Rubric>) => {
    const prompt = value.prompts[pIdx];
    const rubrics = prompt.rubrics.map((r, i) => (i === rIdx ? { ...r, ...patch } : r));
    setPrompt(pIdx, { rubrics });
  };

  const changeQuestionType = (idx: number, qt: QuestionType) => {
    const prompt = value.prompts[idx];
    // Regenerate rubrics from scratch when the type changes so artifact/dimension stay in sync.
    if (prompt.question_type !== qt) {
      setPrompt(idx, { question_type: qt, rubrics: rubricsForType(qt) });
    }
  };

  const addPrompt = () => {
    const id = nextPromptId(value.prompts);
    onChange({ ...value, prompts: [...value.prompts, blankPrompt(id)] });
  };

  const removePrompt = (idx: number) => {
    onChange({ ...value, prompts: value.prompts.filter((_, i) => i !== idx) });
  };

  return (
    <div className="card space-y-5">
      <div>
        <h2 className="text-base font-semibold">Trial design prompts</h2>
        <p className="text-sm text-slate-500">
          For each design element you want to capture, add a question and select its type.
          Rubrics are generated automatically — fill in points / criterion / tolerance for each.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="label">trial_id</label>
          <input
            className="input"
            placeholder="e.g., NCT01234567 or internal trial ID"
            value={value.trial_id}
            onChange={(e) => onChange({ ...value, trial_id: e.target.value })}
          />
        </div>
        <div>
          <label className="label">username</label>
          <input
            className="input"
            placeholder="e.g., jdoe"
            value={value.username}
            onChange={(e) => onChange({ ...value, username: e.target.value })}
          />
        </div>
      </div>

      <div className="space-y-4">
        {value.prompts.length === 0 && (
          <div className="rounded-md border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
            No questions yet. Click "Add question" to begin.
          </div>
        )}

        {value.prompts.map((p, idx) => (
          <div key={idx} className="border border-slate-200 rounded-md p-4 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-slate-500">id</span>
                <input
                  className="input !w-32 !py-1 !text-xs font-mono"
                  value={p.id}
                  onChange={(e) => setPrompt(idx, { id: e.target.value })}
                />
              </div>
              <button
                type="button"
                className="text-xs text-rose-600 hover:underline"
                onClick={() => removePrompt(idx)}
              >
                Remove
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="label">design_element</label>
                <select
                  className="input"
                  value={p.design_element}
                  onChange={(e) => setPrompt(idx, { design_element: e.target.value })}
                >
                  <option value="">— select —</option>
                  {DESIGN_ELEMENT_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label">question_type</label>
                <select
                  className="input"
                  value={p.question_type}
                  onChange={(e) => changeQuestionType(idx, e.target.value as QuestionType)}
                >
                  <option value="">— select —</option>
                  {QUESTION_TYPE_OPTIONS.map((opt) => (
                    <option key={opt.v} value={opt.v}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="md:col-span-2">
                <label className="label">question</label>
                <input
                  className="input"
                  placeholder="e.g., Alpha allocated to PFS"
                  value={p.question}
                  onChange={(e) => setPrompt(idx, { question: e.target.value })}
                />
              </div>
            </div>

            {p.rubrics.length > 0 && (
              <div className="border-t border-slate-200 pt-3 space-y-3">
                <div className="text-xs uppercase tracking-wide text-slate-500">
                  Rubrics ({p.rubrics.length})
                </div>
                {p.rubrics.map((r, rIdx) => (
                  <div
                    key={rIdx}
                    className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50"
                  >
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                      <div>
                        <span className="text-slate-500">Artifact:</span>{" "}
                        <code className="font-mono">{r.artifact}</code>
                      </div>
                      {r.dimension && (
                        <div>
                          <span className="text-slate-500">Dimension:</span>{" "}
                          <span className="font-medium">{r.dimension}</span>
                        </div>
                      )}
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="label">points</label>
                        <input
                          className="input"
                          type="number"
                          value={r.points}
                          onChange={(e) =>
                            setRubric(idx, rIdx, { points: e.target.value })
                          }
                        />
                      </div>
                      <div>
                        <label className="label">tolerance</label>
                        <input
                          className="input"
                          value={r.tolerance}
                          onChange={(e) =>
                            setRubric(idx, rIdx, { tolerance: e.target.value })
                          }
                        />
                      </div>
                      <div className="md:col-span-2">
                        <label className="label">criterion</label>
                        <textarea
                          className="input min-h-[60px]"
                          value={r.criterion}
                          onChange={(e) =>
                            setRubric(idx, rIdx, { criterion: e.target.value })
                          }
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}

        <button type="button" className="btn-secondary" onClick={addPrompt}>
          + Add question
        </button>
      </div>
    </div>
  );
}
