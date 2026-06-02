"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import StepCompare from "@/components/StepCompare";
import { Comparison } from "@/lib/types";

const emptyComparison: Comparison = {
  trial_id: "",
  username: "",
  prompts: [],
};

function IntakeApp() {
  const params = useSearchParams();
  const sessionId = params.get("session") || "default";
  const draftKey = `intake-draft:${sessionId}`;

  const [compare, setCompare] = useState<Comparison>(emptyComparison);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState<
    | { kind: "ok"; msg: string; issueUrl?: string; fileUrl?: string }
    | { kind: "err"; msg: string }
    | null
  >(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(draftKey);
      if (raw) setCompare(JSON.parse(raw));
    } catch {
      /* ignore */
    }
  }, [draftKey]);

  const saveDraft = () => {
    try {
      localStorage.setItem(draftKey, JSON.stringify(compare));
      setStatus({ kind: "ok", msg: "Draft saved locally." });
    } catch (e: any) {
      setStatus({ kind: "err", msg: e?.message || "Save failed" });
    }
  };

  const submit = async () => {
    setSubmitting(true);
    setStatus(null);
    try {
      const res = await fetch("/api/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          submittedAt: new Date().toISOString(),
          comparison: compare,
        }),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || "Submit failed");
      setStatus({
        kind: "ok",
        msg: `Submitted as issue #${json.submissionId}.`,
        issueUrl: json.url,
        fileUrl: json.fileUrl,
      });
      try {
        localStorage.removeItem(draftKey);
      } catch {
        /* ignore */
      }
    } catch (e: any) {
      setStatus({ kind: "err", msg: e?.message || "Submit failed" });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <StepCompare value={compare} onChange={setCompare} />

      {status && (
        <div
          className={
            "mt-4 rounded-md px-3 py-2 text-xs border " +
            (status.kind === "ok"
              ? "bg-emerald-50 border-emerald-200 text-emerald-900"
              : "bg-rose-50 border-rose-200 text-rose-900")
          }
        >
          <div>{status.msg}</div>
          {status.kind === "ok" && (status.issueUrl || status.fileUrl) && (
            <div className="mt-1 space-x-3">
              {status.issueUrl && (
                <a
                  href={status.issueUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  View issue
                </a>
              )}
              {status.fileUrl && (
                <a
                  href={status.fileUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  View raw JSON
                </a>
              )}
            </div>
          )}
        </div>
      )}

      <div className="flex items-center justify-end gap-2 mt-6 flex-wrap">
        <button className="btn-secondary" onClick={saveDraft}>
          Save draft
        </button>
        <button className="btn-primary" disabled={submitting} onClick={submit}>
          {submitting ? "Submitting…" : "Submit"}
        </button>
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<div className="card">Loading…</div>}>
      <IntakeApp />
    </Suspense>
  );
}
