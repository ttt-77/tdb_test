"use client";
import { useCallback, useEffect, useMemo, useState } from "react";

type Status = "pending" | "reviewed" | "needs_fix";

type Summary = {
  submissionId: string;
  trial_id: string;
  username: string;
  submittedAt: string;
  status: Status;
  reviewedAt?: string;
};

type Detail = Summary & {
  reviewer?: string;
  reviewerNote?: string;
  comparison: unknown;
};

const statusColor = (s: Status) =>
  s === "reviewed"
    ? "bg-emerald-100 text-emerald-800 border-emerald-200"
    : s === "needs_fix"
    ? "bg-rose-100 text-rose-800 border-rose-200"
    : "bg-amber-100 text-amber-800 border-amber-200";

const PW_KEY = "admin-pw";

export default function AdminPage() {
  const [pw, setPw] = useState("");
  const [authed, setAuthed] = useState(false);
  const [items, setItems] = useState<Summary[]>([]);
  const [filter, setFilter] = useState<"all" | Status>("all");
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [reviewerNote, setReviewerNote] = useState("");

  useEffect(() => {
    const stored = sessionStorage.getItem(PW_KEY) || "";
    if (stored) {
      setPw(stored);
      setAuthed(true);
    }
  }, []);

  const headers = useMemo(
    () => ({ "Content-Type": "application/json", "x-admin-password": pw }),
    [pw],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/submissions", { headers });
      if (res.status === 401) {
        setAuthed(false);
        sessionStorage.removeItem(PW_KEY);
        throw new Error("Wrong password.");
      }
      const json = await res.json();
      if (!json.ok) throw new Error(json.error || "List failed");
      setItems(json.items);
    } catch (e: any) {
      setError(e?.message || "List failed");
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => {
    if (authed) refresh();
  }, [authed, refresh]);

  const openDetail = async (id: string) => {
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    setError(null);
    try {
      const res = await fetch(`/api/${id}`, { headers });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error || "Fetch failed");
      setDetail(json.record);
      setReviewer(json.record.reviewer || "");
      setReviewerNote(json.record.reviewerNote || "");
    } catch (e: any) {
      setError(e?.message || "Fetch failed");
    }
  };

  const setStatus = async (id: string, status: Status) => {
    setError(null);
    try {
      const res = await fetch(`/api/${id}`, {
        method: "PATCH",
        headers,
        body: JSON.stringify({ status, reviewer, reviewerNote }),
      });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error || "Update failed");
      setDetail(json.record);
      setItems((prev) =>
        prev.map((p) =>
          p.submissionId === id ? { ...p, status, reviewedAt: json.record.reviewedAt } : p,
        ),
      );
    } catch (e: any) {
      setError(e?.message || "Update failed");
    }
  };

  const filtered = filter === "all" ? items : items.filter((i) => i.status === filter);

  if (!authed) {
    return (
      <div className="card max-w-sm mx-auto space-y-3">
        <h2 className="text-base font-semibold">Admin login</h2>
        <input
          type="password"
          className="input"
          placeholder="Admin password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              sessionStorage.setItem(PW_KEY, pw);
              setAuthed(true);
            }
          }}
        />
        <button
          className="btn-primary w-full"
          onClick={() => {
            sessionStorage.setItem(PW_KEY, pw);
            setAuthed(true);
          }}
        >
          Continue
        </button>
        {error && (
          <div className="text-xs text-rose-700">{error}</div>
        )}
        <p className="text-xs text-slate-500">
          If <code>ADMIN_PASSWORD</code> is unset on the server, any value (including empty) is accepted.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-base font-semibold">Submissions ({items.length})</h2>
        <div className="flex items-center gap-2 flex-wrap">
          {(["all", "pending", "reviewed", "needs_fix"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={
                "px-2.5 py-1 rounded text-xs border " +
                (filter === s
                  ? "bg-slate-900 text-white border-slate-900"
                  : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100")
              }
            >
              {s}
            </button>
          ))}
          <button className="btn-secondary !py-1 !text-xs" onClick={refresh} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md bg-rose-50 border border-rose-200 px-3 py-2 text-xs text-rose-900">
          {error}
        </div>
      )}

      {filtered.length === 0 && !loading && (
        <div className="text-sm text-slate-500">No submissions match this filter.</div>
      )}

      <ul className="space-y-2">
        {filtered.map((it) => {
          const isOpen = openId === it.submissionId;
          return (
            <li key={it.submissionId} className="card !p-0 overflow-hidden">
              <button
                className="w-full flex items-center justify-between gap-3 p-3 text-left hover:bg-slate-50"
                onClick={() => openDetail(it.submissionId)}
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span
                    className={
                      "px-2 py-0.5 rounded text-xs border font-medium " + statusColor(it.status)
                    }
                  >
                    {it.status}
                  </span>
                  <div className="truncate">
                    <div className="text-sm font-medium truncate">
                      {it.trial_id}{" "}
                      <span className="text-slate-500 font-normal">— {it.username}</span>
                    </div>
                    <div className="text-xs text-slate-500 truncate">
                      {it.submittedAt}
                    </div>
                  </div>
                </div>
                <span className="text-xs text-slate-400">{isOpen ? "▲" : "▼"}</span>
              </button>

              {isOpen && (
                <div className="border-t border-slate-200 p-4 bg-slate-50 space-y-3">
                  {!detail && <div className="text-xs text-slate-500">Loading…</div>}
                  {detail && (
                    <>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <div>
                          <label className="label">Reviewer</label>
                          <input
                            className="input"
                            value={reviewer}
                            onChange={(e) => setReviewer(e.target.value)}
                            placeholder="your name"
                          />
                        </div>
                        <div>
                          <label className="label">Reviewer note</label>
                          <input
                            className="input"
                            value={reviewerNote}
                            onChange={(e) => setReviewerNote(e.target.value)}
                          />
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-slate-500 mr-1">Set status:</span>
                        {(["pending", "reviewed", "needs_fix"] as const).map((s) => (
                          <button
                            key={s}
                            className={
                              "px-2.5 py-1 rounded text-xs border " +
                              (detail.status === s
                                ? statusColor(s)
                                : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100")
                            }
                            onClick={() => setStatus(it.submissionId, s)}
                          >
                            {s}
                          </button>
                        ))}
                        {detail.reviewedAt && (
                          <span className="text-xs text-slate-500 ml-2">
                            last updated {detail.reviewedAt}
                          </span>
                        )}
                      </div>
                      <details className="text-xs">
                        <summary className="cursor-pointer text-slate-600">
                          Raw submission JSON
                        </summary>
                        <pre className="mt-2 p-3 rounded bg-white border border-slate-200 overflow-x-auto text-[11px]">
                          {JSON.stringify(detail, null, 2)}
                        </pre>
                      </details>
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
