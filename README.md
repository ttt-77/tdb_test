# Clinical Trial AI Reproduction Benchmark — Intake

A Next.js + Tailwind intake form for trial statisticians. Submissions are saved to a **Hugging Face Dataset** repo. An `/admin` page lets reviewers triage submissions (pending / reviewed / needs_fix).

## What it does

- **Public form (`/`)** — statisticians enter `trial_id`, `username`, and a list of questions. Each question has `design_element` (dropdown), `question_type` (dropdown), and an auto-generated rubric block. Buttons: **Save draft** (localStorage), **Submit** (writes to HF Dataset).
- **Admin (`/admin`)** — password-gated review console. Lists every submission with its current status, lets you change status, add a reviewer name + note. Updates are committed back to the dataset.

## Run locally

```bash
npm install
npm run dev
# open http://localhost:3000
```

Without HF env vars set, submissions land in `./data/submissions/<...>.json` on disk — fine for dev.

## Deploy on Vercel + store on Hugging Face

### 1. Create a private HF Dataset repo

- Sign in at <https://huggingface.co>
- Click your avatar → **New Dataset**
- Owner: your username (e.g. `ttt-77`)
- Name: e.g. `tdb-intake-submissions`
- Visibility: **Private**
- Create. Leave it empty — files will appear as submissions arrive.

### 2. Generate an HF access token

- <https://huggingface.co/settings/tokens> → **New token**
- Type: **Write**
- Save the `hf_...` string.

### 3. Push this repo to GitHub and import on Vercel

```bash
git push
```

Then go to <https://vercel.com>, import the repo (auto-detects Next.js).

### 4. Add env vars in Vercel

Project → Settings → Environment Variables (set for Production + Preview + Development):

| Name | Value | Required |
| --- | --- | --- |
| `HF_TOKEN` | the token from step 2 | ✅ |
| `HF_DATASET_REPO` | `ttt-77/tdb-intake-submissions` | ✅ |
| `HF_DATASET_BRANCH` | `main` (default) | optional |
| `ADMIN_PASSWORD` | a password you'll give to reviewers | ✅ for `/admin` |

Redeploy after saving.

### 5. Test

- Open the deployed URL, fill the form, **Submit**. A new file appears in the HF dataset at `submissions/<trial_id>__<username>__<timestamp>.json`.
- Open `/admin`, enter the password, you'll see the new submission with status `pending`. Click to expand, set reviewer/note, change status — every change becomes a commit on the dataset.

## Submission record shape

Each `submissions/*.json` file looks like:

```json
{
  "submissionId": "submissions/NCT0001__jdoe__2026-06-01T...Z.json",
  "submittedAt": "2026-06-01T...Z",
  "trial_id": "NCT0001",
  "username": "jdoe",
  "status": "pending",
  "reviewer": "",
  "reviewerNote": "",
  "reviewedAt": "...",
  "comparison": { ... full form payload ... }
}
```

Load all submissions in Python:

```python
from huggingface_hub import HfApi
api = HfApi()
# Either clone the dataset:
#   from huggingface_hub import snapshot_download
#   snapshot_download("ttt-77/tdb-intake-submissions", repo_type="dataset")
# Or list the files via API and read each.
```

## Privacy notes

- The dataset repo should be **private**.
- `HF_TOKEN` lives only in Vercel env vars — never commit it.
- Rotate the token periodically; update the env var and redeploy.
- `ADMIN_PASSWORD` is a shared secret — anyone with it can change submission statuses.

## Project structure

```text
app/
  layout.tsx, page.tsx, globals.css      — public form
  admin/page.tsx                         — review console
  api/submit/route.ts                    POST — create submission
  api/submissions/route.ts               GET  — list submissions (admin)
  api/submissions/[...path]/route.ts     GET, PATCH — one submission (admin)
components/
  StepCompare.tsx
lib/
  types.ts, storage.ts
data/submissions/                        — created at runtime in dev only
```
