"""The agentic loop: give the model a workspace + tools and let it work.

Unlike a single LLM call, the model here explores the SAP with tools (grep /
read in chunks), writes output.json and output.R into the workspace, and can run
the R script to verify it before finishing.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from lib.agent import SYSTEM_PROMPT, provider_for
from lib.tools import Workspace, anthropic_tools, openai_tools

# Appended to the task so the model knows it is working in a workspace.
AGENT_TASK_TEMPLATE = """Your workspace contains the trial's source document as a text file with page
markers (lines like `===== Page 12 =====`). The document is large — do NOT try to
read it all at once. Use `grep` to locate the relevant sections, then `read_file`
around those line numbers. Start by calling `list_files`.

Answer these evaluation questions:

```json
{prompt_block}
```

Then write two files into the workspace:

1. `output.json` — a JSON object with a single top-level key `"output"` whose
   value is the prompt block above with every `null` replaced by your extracted
   or derived result. Do not add, remove, rename, or modify any other fields.
2. `output.R` — R code implementing the calculations for every
   "derivation_required" question. For each one it must print (1) the source
   inputs, (2) the calculation method and formula applied, and (3) the final
   calculated value.

After writing `output.R`, run it with `run_r` and fix it until it executes
without errors and prints values consistent with `output.json`. If Rscript is
unavailable, say so and continue.

When both files are written (and R verified if possible), reply with a short
summary of what you found and any values that were absent from the document.
"""


def _max_tokens(effort: Optional[str]) -> int:
    # Thinking tokens count against max_tokens, so give high effort room.
    return 64000 if effort in ("xhigh", "max") else 16000


def run_agent(
    model: str,
    workspace: Workspace,
    prompt_block: Dict[str, Any],
    api_key: Optional[str] = None,
    effort: Optional[str] = None,
    max_iterations: int = 30,
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Run the agent loop until it stops calling tools (or hits max_iterations).

    Returns {"steps": [...], "final_text": str, "iterations": int,
             "stopped_early": bool, "usage": {...}}
    """
    task = AGENT_TASK_TEMPLATE.format(
        prompt_block=json.dumps(prompt_block, indent=2, ensure_ascii=False)
    )

    def emit(ev: Dict[str, Any]) -> None:
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    provider = provider_for(model)
    if provider == "anthropic":
        return _run_anthropic(
            model, workspace, task, api_key, effort, max_iterations, emit
        )
    if provider == "openai":
        return _run_openai(model, workspace, task, api_key, effort, max_iterations, emit)
    raise ValueError(
        f"Unknown model '{model}' — use a claude-* (Anthropic) or gpt-*/o*-* (OpenAI) id."
    )


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def _run_anthropic(
    model: str,
    ws: Workspace,
    task: str,
    api_key: Optional[str],
    effort: Optional[str],
    max_iterations: int,
    emit: Callable[[Dict[str, Any]], None],
) -> Dict[str, Any]:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key) if api_key else Anthropic()
    tools = anthropic_tools()
    messages: List[Dict[str, Any]] = [{"role": "user", "content": task}]
    steps: List[Dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    final_text = ""
    stopped_early = True
    i = 0

    for i in range(1, max_iterations + 1):
        kwargs: Dict[str, Any] = {}
        if effort:
            kwargs["output_config"] = {"effort": effort}
        resp = client.messages.create(
            model=model,
            max_tokens=_max_tokens(effort),
            system=[{"type": "text", "text": SYSTEM_PROMPT}],
            tools=tools,
            messages=messages,
            **kwargs,
        )
        if getattr(resp, "usage", None):
            usage["input_tokens"] += getattr(resp.usage, "input_tokens", 0) or 0
            usage["output_tokens"] += getattr(resp.usage, "output_tokens", 0) or 0

        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
        if text:
            steps.append({"kind": "text", "iteration": i, "text": text})
            emit({"kind": "text", "iteration": i, "text": text})

        # Keep the assistant turn verbatim (preserves any thinking blocks).
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not tool_uses:
            final_text = text
            stopped_early = False
            break

        results = []
        for tu in tool_uses:
            args = dict(tu.input or {})
            out = ws.call(tu.name, args)
            steps.append(
                {"kind": "tool", "iteration": i, "name": tu.name, "args": args, "result": out}
            )
            emit({"kind": "tool", "iteration": i, "name": tu.name, "args": args, "result": out})
            results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": out}
            )
        messages.append({"role": "user", "content": results})

    return {
        "steps": steps,
        "final_text": final_text,
        "iterations": i,
        "stopped_early": stopped_early,
        "usage": usage,
    }


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def _run_openai(
    model: str,
    ws: Workspace,
    task: str,
    api_key: Optional[str],
    effort: Optional[str],
    max_iterations: int,
    emit: Callable[[Dict[str, Any]], None],
) -> Dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    tools = openai_tools()
    messages: List[Any] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    steps: List[Dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    final_text = ""
    stopped_early = True
    i = 0

    for i in range(1, max_iterations + 1):
        kwargs: Dict[str, Any] = {}
        if effort:
            kwargs["reasoning_effort"] = effort
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=tools, **kwargs
        )
        if getattr(resp, "usage", None):
            usage["input_tokens"] += getattr(resp.usage, "prompt_tokens", 0) or 0
            usage["output_tokens"] += getattr(resp.usage, "completion_tokens", 0) or 0

        msg = resp.choices[0].message
        text = (msg.content or "").strip()
        if text:
            steps.append({"kind": "text", "iteration": i, "text": text})
            emit({"kind": "text", "iteration": i, "text": text})

        messages.append(msg)

        calls = list(msg.tool_calls or [])
        if not calls:
            final_text = text
            stopped_early = False
            break

        for tc in calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            out = ws.call(tc.function.name, args)
            steps.append(
                {
                    "kind": "tool",
                    "iteration": i,
                    "name": tc.function.name,
                    "args": args,
                    "result": out,
                }
            )
            emit(
                {
                    "kind": "tool",
                    "iteration": i,
                    "name": tc.function.name,
                    "args": args,
                    "result": out,
                }
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})

    return {
        "steps": steps,
        "final_text": final_text,
        "iterations": i,
        "stopped_early": stopped_early,
        "usage": usage,
    }
