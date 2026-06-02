// Storage backend: Hugging Face Dataset repo (production) or local filesystem (dev).
//
// In production set these env vars in Vercel:
//   HF_TOKEN              — HF user access token with "Write" permission on the dataset repo
//   HF_DATASET_REPO       — e.g. "ttt-77/tdb-intake-submissions"
//   HF_DATASET_BRANCH     — optional, defaults to "main"
//
// If HF_TOKEN or HF_DATASET_REPO is missing, the code falls back to ./data/submissions/*.json
// on disk so local development still works without HF credentials.

import { promises as fs } from "fs";
import path from "path";

const HF_BASE = "https://huggingface.co";
const HF_TOKEN = process.env.HF_TOKEN;
const HF_DATASET = process.env.HF_DATASET_REPO;
const HF_BRANCH = process.env.HF_DATASET_BRANCH || "main";

export const hfConfigured = !!(HF_TOKEN && HF_DATASET);

const safe = (s: string) => (s || "").trim().replace(/[^a-zA-Z0-9-_]/g, "_");

// ---- Types ----------------------------------------------------------------

export type SubmissionStatus = "pending" | "reviewed" | "needs_fix";

export type SubmissionRecord = {
  submissionId: string;        // == path inside the dataset repo, e.g. "submissions/foo.json"
  submittedAt: string;
  trial_id: string;
  username: string;
  status: SubmissionStatus;
  reviewedAt?: string;
  reviewer?: string;
  reviewerNote?: string;
  comparison: unknown;
};

export type CreateSubmissionInput = {
  trial_id: string;
  username: string;
  submittedAt: string;
  comparison: unknown;
};

export type CreateSubmissionResult = {
  submissionId: string;
  url?: string;
};

export type SubmissionSummary = {
  submissionId: string;
  trial_id: string;
  username: string;
  submittedAt: string;
  status: SubmissionStatus;
  reviewedAt?: string;
};

// ---- HF helpers -----------------------------------------------------------

async function hfCommit(
  filePath: string,
  contentBase64: string,
  summary: string,
): Promise<void> {
  // HF commit API takes NDJSON: a header line, then one file line per upload.
  const url = `${HF_BASE}/api/datasets/${HF_DATASET}/commit/${HF_BRANCH}`;
  const body =
    JSON.stringify({ key: "header", value: { summary, description: "" } }) +
    "\n" +
    JSON.stringify({
      key: "file",
      value: { path: filePath, encoding: "base64", content: contentBase64 },
    }) +
    "\n";
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${HF_TOKEN}`,
      "Content-Type": "application/x-ndjson",
    },
    body,
  });
  if (!res.ok) {
    throw new Error(`HF commit failed (${res.status}): ${await res.text()}`);
  }
}

async function hfReadFile(filePath: string): Promise<string | null> {
  const url = `${HF_BASE}/datasets/${HF_DATASET}/resolve/${HF_BRANCH}/${filePath}`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${HF_TOKEN}` },
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.text();
}

async function hfListSubmissions(): Promise<string[]> {
  const url = `${HF_BASE}/api/datasets/${HF_DATASET}/tree/${HF_BRANCH}/submissions`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${HF_TOKEN}` },
    cache: "no-store",
  });
  if (res.status === 404) return [];
  if (!res.ok) {
    throw new Error(`HF tree failed (${res.status}): ${await res.text()}`);
  }
  const items = (await res.json()) as Array<{ path: string; type: string }>;
  return items
    .filter((i) => i.type === "file" && i.path.endsWith(".json"))
    .map((i) => i.path);
}

// ---- Public API -----------------------------------------------------------

export async function createSubmission(
  input: CreateSubmissionInput,
): Promise<CreateSubmissionResult> {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const fileName = `${safe(input.trial_id)}__${safe(input.username)}__${stamp}.json`;
  const submissionId = `submissions/${fileName}`;

  const record: SubmissionRecord = {
    submissionId,
    submittedAt: input.submittedAt,
    trial_id: input.trial_id,
    username: input.username,
    status: "pending",
    comparison: input.comparison,
  };
  const content = JSON.stringify(record, null, 2);

  if (hfConfigured) {
    await hfCommit(
      submissionId,
      Buffer.from(content, "utf-8").toString("base64"),
      `Add submission: ${input.trial_id} — ${input.username}`,
    );
    return {
      submissionId,
      url: `${HF_BASE}/datasets/${HF_DATASET}/blob/${HF_BRANCH}/${submissionId}`,
    };
  }

  const dir = path.join(process.cwd(), "data", "submissions");
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, fileName), content, "utf-8");
  return { submissionId };
}

export async function listSubmissions(): Promise<SubmissionSummary[]> {
  if (hfConfigured) {
    const paths = await hfListSubmissions();
    const records = await Promise.all(
      paths.map(async (p) => {
        const text = await hfReadFile(p);
        if (!text) return null;
        try {
          const r = JSON.parse(text) as SubmissionRecord;
          return summarize(r);
        } catch {
          return null;
        }
      }),
    );
    return records.filter((x): x is SubmissionSummary => x !== null);
  }

  const dir = path.join(process.cwd(), "data", "submissions");
  let files: string[] = [];
  try {
    files = await fs.readdir(dir);
  } catch {
    return [];
  }
  const records = await Promise.all(
    files
      .filter((f) => f.endsWith(".json"))
      .map(async (f) => {
        try {
          const raw = await fs.readFile(path.join(dir, f), "utf-8");
          return summarize(JSON.parse(raw));
        } catch {
          return null;
        }
      }),
  );
  return records.filter((x): x is SubmissionSummary => x !== null);
}

export async function getSubmission(submissionId: string): Promise<SubmissionRecord | null> {
  if (!submissionId.startsWith("submissions/")) return null;
  if (hfConfigured) {
    const txt = await hfReadFile(submissionId);
    if (!txt) return null;
    try {
      return JSON.parse(txt);
    } catch {
      return null;
    }
  }
  const fullPath = path.join(process.cwd(), "data", submissionId);
  try {
    return JSON.parse(await fs.readFile(fullPath, "utf-8"));
  } catch {
    return null;
  }
}

export async function updateSubmission(
  submissionId: string,
  patch: Partial<Pick<SubmissionRecord, "status" | "reviewer" | "reviewerNote">>,
): Promise<SubmissionRecord | null> {
  const existing = await getSubmission(submissionId);
  if (!existing) return null;
  const updated: SubmissionRecord = {
    ...existing,
    ...patch,
    reviewedAt: patch.status ? new Date().toISOString() : existing.reviewedAt,
  };
  const content = JSON.stringify(updated, null, 2);

  if (hfConfigured) {
    await hfCommit(
      submissionId,
      Buffer.from(content, "utf-8").toString("base64"),
      `Update: ${submissionId.split("/").pop()}`,
    );
    return updated;
  }
  const fullPath = path.join(process.cwd(), "data", submissionId);
  await fs.writeFile(fullPath, content, "utf-8");
  return updated;
}

function summarize(r: SubmissionRecord): SubmissionSummary {
  return {
    submissionId: r.submissionId,
    trial_id: r.trial_id,
    username: r.username,
    submittedAt: r.submittedAt,
    status: r.status ?? "pending",
    reviewedAt: r.reviewedAt,
  };
}

// ---- Admin gate (shared password) ----------------------------------------

export const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";

export function isAdminAuthorized(req: Request): boolean {
  if (!ADMIN_PASSWORD) return true; // no password set = open (dev mode)
  const header = req.headers.get("x-admin-password") || "";
  return header === ADMIN_PASSWORD;
}
