// Storage backend: GitHub Issues (production) or local filesystem (dev fallback).
//
// In production set the following env vars (Vercel → Project → Settings → Environment Variables):
//   GITHUB_TOKEN  — fine-grained PAT with Issues: read/write on the submissions repo
//   GITHUB_OWNER  — repo owner (e.g. ttt-77)
//   GITHUB_REPO   — repo name (e.g. tdb-intake-submissions)
//
// If any of those are missing, writes go to ./data/submissions/<id>/*.json on disk.

import { promises as fs } from "fs";
import path from "path";

const token = process.env.GITHUB_TOKEN;
const owner = process.env.GITHUB_OWNER;
const repo = process.env.GITHUB_REPO;
export const githubConfigured = !!(token && owner && repo);

const safe = (s: string) => (s || "").trim().replace(/[^a-zA-Z0-9-_]/g, "_");

async function gh<T = any>(pathname: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`https://api.github.com${pathname}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`GitHub ${res.status}: ${text}`);
  }
  return res.json();
}

// ---- Submissions ----------------------------------------------------------

export type CreateSubmissionInput = {
  trial_id: string;
  username: string;
  submittedAt: string;
  comparison: unknown;
};

export type CreateSubmissionResult = { submissionId: string; url?: string };

export async function createSubmission(
  input: CreateSubmissionInput,
): Promise<CreateSubmissionResult & { fileUrl?: string }> {
  if (githubConfigured) {
    // 1. Commit the raw submission JSON to the repo.
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const baseName = `${safe(input.trial_id)}__${safe(input.username)}__${stamp}`;
    const filePath = `submissions/${baseName}.json`;
    const fileContent = JSON.stringify(input, null, 2);
    const commitMsg = `Add submission: ${input.trial_id} — ${input.username}`;
    const fileRes = await gh<{ content: { html_url: string } }>(
      `/repos/${owner}/${repo}/contents/${encodeURIComponent(filePath).replace(/%2F/g, "/")}`,
      {
        method: "PUT",
        body: JSON.stringify({
          message: commitMsg,
          content: Buffer.from(fileContent, "utf-8").toString("base64"),
        }),
      },
    );

    // 2. Open an issue referencing the file.
    const title = `[Intake] ${input.trial_id} — ${input.username}`;
    const body = renderIssueBody(input, fileRes.content.html_url);
    const issue = await gh<{ number: number; html_url: string }>(
      `/repos/${owner}/${repo}/issues`,
      {
        method: "POST",
        body: JSON.stringify({ title, body, labels: ["intake-submission"] }),
      },
    );
    return {
      submissionId: String(issue.number),
      url: issue.html_url,
      fileUrl: fileRes.content.html_url,
    };
  }

  // local fs fallback
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const submissionId = `${safe(input.trial_id)}__${safe(input.username)}__${stamp}`;
  const dir = path.join(process.cwd(), "data", "submissions", submissionId);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(
    path.join(dir, "prompts.json"),
    JSON.stringify({ ...input, submissionId }, null, 2),
    "utf-8",
  );
  return { submissionId };
}

function renderIssueBody(input: CreateSubmissionInput, fileUrl?: string): string {
  const c = input.comparison as { prompts?: any[] };
  const rows = (c?.prompts ?? [])
    .map(
      (p: any) =>
        `| \`${p.id}\` | ${escapeCell(p.design_element)} | ${escapeCell(p.question)} | \`${
          p.question_type
        }\` |`,
    )
    .join("\n");
  const table =
    rows.length > 0
      ? `| id | design_element | question | question_type |\n|---|---|---|---|\n${rows}`
      : "_No questions submitted._";

  return [
    `**Submitted:** ${input.submittedAt}`,
    `**trial_id:** \`${input.trial_id}\``,
    `**username:** \`${input.username}\``,
    fileUrl ? `**Raw submission file:** ${fileUrl}` : "",
    "",
    "### Questions",
    "",
    table,
    "",
    "### Raw submission",
    "",
    "```json",
    JSON.stringify(input, null, 2),
    "```",
  ]
    .filter(Boolean)
    .join("\n");
}

function escapeCell(s: string | undefined): string {
  return (s || "").replace(/\|/g, "\\|").replace(/\n/g, " ");
}

