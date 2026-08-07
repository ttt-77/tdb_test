# LLM runner — populate a submission's answers

Standalone script that runs one or more LLMs over an intake submission's
questions, using the trial's parsed SAP as the only source, and writes the
completed `output.json` (+ `output.R`) per model to local files.

The script only **reads** from HF and **writes outputs locally** — it never
uploads anything to Hugging Face. You can also run it **fully local** (local
input files, no HF access, no `HF_TOKEN`).

## Setup (uv)

The script declares its dependencies inline (PEP 723), so `uv run` creates an
isolated environment and installs them automatically — no manual venv/pip.

```bash
# install uv once: https://docs.astral.sh/uv/  (curl -LsSf https://astral.sh/uv/install.sh | sh)
export ANTHROPIC_API_KEY=...      # for claude-* models
export OPENAI_API_KEY=...         # for gpt-* models
# only if you read inputs from HF (not needed for fully-local mode):
export HF_TOKEN=hf_...            # read access to the private intake_form_data repo

uv run run_llm.py --submission NCT02578680__EricZ
```

(Prefer plain pip? `pip install -r requirements.txt` then `python run_llm.py ...`.)

## Run — fully local (no HF)

Point it at a local submission JSON and a local SAP file:

```bash
python run_llm.py \
    --submission NCT02578680__EricZ \
    --submission-file ./sub.json \
    --sap-file ./sap.lines.json \
    --models claude-opus-5 gpt-5.6-sol
```

- `--submission-file` — a submission record, or a bare `{trial_id, username, prompts}`.
- `--sap-file` — a `sap.lines.json` (rebuilt with page markers) **or** any
  `.txt`/`.md` (used as-is).
- `--submission` is still used only to name the output folder.

## Run — reading inputs from HF

```bash
python run_llm.py --submission NCT02578680__EricZ
# pin an exact submission version (default is the latest):
python run_llm.py --submission NCT02578680__EricZ \
    --version 2026-06-07T17-23-05-870000+00-00.json
# pick a specific SAP doc + models:
python run_llm.py --submission NCT02578680__EricZ \
    --doc-id 10.1056_nejmoa1801005 \
    --models claude-opus-5 gpt-5.6-sol
```

`--version` accepts the bare filename, a full repo path, or a URL-encoded `%2B`
(it normalizes to the literal `+`).

## What it does

1. **Submission** — loads the latest version of
   `submissions/<submission>/<stamp>.json` from `trialdesignbench/intake_form_data`.
2. **SAP** — maps the NCT id (prefix of the submission name) to a
   `documents/<doi>` folder via `data/tdr.parquet` (one NCT can map to several
   docs — use `--doc-id` to pick), then rebuilds SAP text **with page markers**
   from `sap.lines.json` (so answers can cite page numbers, as the prompt
   requires).
3. **Prompt block** — turns the questions into the null-placeholder block
   (`extraction_only` → `extracted_value: null`; `derivation_required` →
   `dimensions: {inputs_used, method, calculated_value}`).
4. **Models** — sends the task prompt (system) + SAP + prompt block to each
   model and parses the returned ```json``` (output.json) and ```r``` (output.R)
   blocks.

## Outputs

```
out/<submission>/
  prompt_block.json          # the null block the models were asked to fill
  sap.txt                    # SAP text fed to the models (with page markers)
  <model>/output.json        # completed block
  <model>/output.R           # R for derivation questions
  <model>/raw.txt            # raw model response (for debugging)
  <model>/error.txt          # present only if the call failed
```

## Notes / caveats

- **Context size:** parsed SAPs are large (e.g. NCT02578680 ≈ 480K chars ≈
  ~120K tokens). Claude 5 models (1M ctx) and GPT-5.6 handle this fine; older or
  smaller-context models may not. The script does not truncate.
- **Reasoning effort:** `--effort` sets how much the model reasons —
  `low|medium|high|xhigh|max` for Anthropic (sent as `output_config.effort`) and
  `none|low|medium|high|xhigh|max` for OpenAI (sent as `reasoning_effort`).
  Omit it to use the model's default (`high` for Claude). Note Claude Haiku 4.5
  does not support effort.
- **Closed-book:** the prompt forbids outside knowledge; the script only ever
  sends the one SAP document.
- **Models are pluggable:** any `claude-*` id routes to Anthropic, any
  `gpt-*`/`o1-*`/`o3-*` to OpenAI. Edit `DEFAULT_MODELS` or pass `--models`.
- Anthropic calls cache the SAP-bearing block, so re-runs are cheaper.
```
