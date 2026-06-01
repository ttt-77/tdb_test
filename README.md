# Clinical Trial AI Reproduction Benchmark — Intake

A Next.js + Tailwind single-page intake form. Statisticians enter:

- `trial_id`, `username`
- A list of questions, each with `design_element`, `question_type` (`extraction_only` / `derivation_required`), and an Evaluation block (`artifact`, `dimension`, `points`, `criterion`, `tolerance`).

Buttons:

- **Save draft** — persists current form to `localStorage`.
- **Submit** — opens a GitHub issue and commits the raw JSON file to the repo.

## Run locally

```bash
npm install
npm run dev
# open http://localhost:3000
```

Without GitHub env vars set, submissions fall back to `./data/submissions/<trial_id>__<username>__<timestamp>/prompts.json` on disk.

## Deploy on Vercel + store submissions on GitHub

### 1. Create a private submissions repo

Create an empty repo, e.g. `ttt-77/tdb-intake-submissions`. Every submission will:

- open a new **issue** there (Markdown summary + raw JSON in a fenced block).
- commit the raw JSON file to `submissions/<trial_id>__<username>__<timestamp>.json` in the same repo.

### 2. Make a fine-grained PAT

GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token.

- **Repository access:** only the submissions repo.
- **Permissions → Repository:**
  - **Contents:** Read and write (for the file commit).
  - **Issues:** Read and write (for the issue).

Save the token — you'll paste it into Vercel next.

### 3. Push this repo to GitHub and import on Vercel

```bash
git add . && git commit -m "Initial intake form"
git push -u origin main
```

Then go to <https://vercel.com>, import the repo (auto-detects Next.js).

### 4. Add env vars in Vercel

Project → Settings → Environment Variables (set for Production + Preview):

| Name | Value |
| --- | --- |
| `GITHUB_TOKEN` | the fine-grained PAT from step 2 |
| `GITHUB_OWNER` | repo owner, e.g. `ttt-77` |
| `GITHUB_REPO` | repo name, e.g. `tdb-intake-submissions` |

Redeploy after saving.

### 5. Test

Fill the form on the deployed URL → **Submit**. In your submissions repo you should see:

- A new file at `submissions/<trial_id>__<username>__<timestamp>.json`.
- A new issue titled `[Intake] <trial_id> — <username>`, labeled `intake-submission`, linking to that file.

The UI shows links to both right after submit.

## Privacy notes

- The submissions repo should be **private**.
- `GITHUB_TOKEN` lives only in Vercel env vars — never commit it.
- Rotate the PAT periodically; update the env var and redeploy.

## Project structure

```text
app/
  layout.tsx, page.tsx, globals.css
  api/submit/route.ts  POST — commits JSON file + opens issue (or writes locally)
components/
  StepCompare.tsx
lib/
  types.ts, storage.ts
data/submissions/      — created at runtime in dev (local fallback only)
```
