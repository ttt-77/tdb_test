"use client";
import { Comparison, PromptItem, QuestionType } from "@/lib/types";

type Props = {
  value: Comparison;
  onChange: (v: Comparison) => void;
};

const questionTypeOptions: { v: QuestionType; label: string }[] = [
  { v: "extraction_only", label: "extraction_only" },
  { v: "derivation_required", label: "derivation_required" },
];

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
  artifact: "",
  dimension: "",
  points: "",
  criterion: "",
  tolerance: "",
});

export default function StepCompare({ value, onChange }: Props) {
  const setPrompt = (idx: number, patch: Partial<PromptItem>) => {
    const next = value.prompts.map((p, i) => (i === idx ? { ...p, ...patch } : p));
    onChange({ ...value, prompts: next });
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
          For each design element you want to capture, add a prompt with its question and
          question type. Use <code>extraction_only</code> when the answer can be pulled verbatim
          from the SAP, and <code>derivation_required</code> when it must be computed.
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
                <input
                  className="input"
                  placeholder="e.g., Alpha allocation"
                  value={p.design_element}
                  onChange={(e) => setPrompt(idx, { design_element: e.target.value })}
                />
              </div>
              <div>
                <label className="label">question_type</label>
                <div className="flex flex-wrap gap-2">
                  {questionTypeOptions.map((o) => (
                    <button
                      key={o.v}
                      type="button"
                      onClick={() => setPrompt(idx, { question_type: o.v })}
                      className={
                        "px-3 py-1.5 rounded-md border text-sm font-mono " +
                        (p.question_type === o.v
                          ? "bg-slate-900 text-white border-slate-900"
                          : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100")
                      }
                    >
                      {o.label}
                    </button>
                  ))}
                </div>
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

            <div className="border-t border-slate-200 pt-3 space-y-3">
              <div className="text-xs uppercase tracking-wide text-slate-500">
                Evaluation
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="label">artifact</label>
                  <input
                    className="input"
                    value={p.artifact}
                    onChange={(e) => setPrompt(idx, { artifact: e.target.value })}
                  />
                </div>
                <div>
                  <label className="label">dimension</label>
                  <input
                    className="input"
                    value={p.dimension}
                    onChange={(e) => setPrompt(idx, { dimension: e.target.value })}
                  />
                </div>
                <div>
                  <label className="label">points</label>
                  <input
                    className="input"
                    type="number"
                    value={p.points}
                    onChange={(e) => setPrompt(idx, { points: e.target.value })}
                  />
                </div>
                <div>
                  <label className="label">tolerance</label>
                  <input
                    className="input"
                    value={p.tolerance}
                    onChange={(e) => setPrompt(idx, { tolerance: e.target.value })}
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="label">criterion</label>
                  <textarea
                    className="input min-h-[60px]"
                    value={p.criterion}
                    onChange={(e) => setPrompt(idx, { criterion: e.target.value })}
                  />
                </div>
              </div>
            </div>
          </div>
        ))}

        <button type="button" className="btn-secondary" onClick={addPrompt}>
          + Add question
        </button>
      </div>
    </div>
  );
}
