# LLM runner — populate a submission's answers

Standalone script that runs one or more LLMs over an intake submission's
questions, using the trial's parsed SAP as the only source, and writes the
completed `output.json` (+ `output.R`) per model to local files.

The script only **reads** from HF and **writes outputs locally** — it never
uploads anything to Hugging Face. You can also run it **fully local** (local
input files, no HF access, no `HF_TOKEN`).

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...      # for claude-* models
export OPENAI_API_KEY=...         # for gpt-* models
# only if you read inputs from HF (not needed for fully-local mode):
export HF_TOKEN=hf_...            # read access to the private intake_form_data repo
```

## Run — fully local (no HF)

Point it at a local submission JSON and a local SAP file:

```bash
python run_llm.py \
    --submission NCT02578680__EricZ \
    --submission-file ./sub.json \
    --sap-file ./sap.lines.json \
    --models claude-opus-4-8 gpt-4o
```

- `--submission-file` — a submission record, or a bare `{trial_id, username, prompts}`.
- `--sap-file` — a `sap.lines.json` (rebuilt with page markers) **or** any
  `.txt`/`.md` (used as-is).
- `--submission` is still used only to name the output folder.

## Run — reading inputs from HF

```bash
python run_llm.py --submission NCT02578680__EricZ
# pick a specific SAP doc + models:
python run_llm.py --submission NCT02578680__EricZ \
    --doc-id 10.1056_nejmoa1801005 \
    --models claude-opus-4-8 gpt-4o
```

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
  ~120K tokens). That fits Claude (large context) but may exceed smaller
  context windows (e.g. gpt-4o's 128K). For those, use a long-context model,
  or pre-trim the SAP. The script does not truncate.
- **Closed-book:** the prompt forbids outside knowledge; the script only ever
  sends the one SAP document.
- **Models are pluggable:** any `claude-*` id routes to Anthropic, any
  `gpt-*`/`o1-*`/`o3-*` to OpenAI. Edit `DEFAULT_MODELS` or pass `--models`.
- Anthropic calls cache the SAP-bearing block, so re-runs are cheaper.
```
