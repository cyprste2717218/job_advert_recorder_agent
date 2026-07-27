"""Drop-in replacement for `adk run` that renders RequestInput as an
interactive terminal select / multi-select via `questionary` instead of a
bare `input()` prompt.

Usage (from the repo root, same args as `adk run`):

    uv run python run_cli_select.py .                 # interactive mode
    uv run python run_cli_select.py . "https://..."    # single-step mode

It works by monkeypatching ``google.adk.cli.cli._prompt_for_function_call``
-- the function ``run_interactively``/``run_once_cli`` call, unqualified,
to resolve a pending RequestInput/RequestConfirmation into a
FunctionResponse -- and then delegating to ADK's own `run_cli`/
`run_once_cli`. Session persistence, --replay, --resume, --jsonl etc. all
keep working unchanged; only the prompting UI is swapped.

To offer real choices (not just free text), have your RequestInput's
payload carry them:

    yield RequestInput(
        message="Which sheet?",
        response_schema=list[str],  # or str for single-select
        payload={"choices": ["Sheet1", "Sheet2", "Sheet3"]},
    )

response_schema of `list[...]` (or a JSON-schema dict with
`"type": "array"`) renders as a checkbox multi-select; anything else with
choices renders as a single select; with no choices it falls back to a
plain text prompt (or a yes/no confirm for RequestConfirmation).
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from typing import Any

import click
import questionary
from google.adk.cli import cli as adk_cli
from google.genai import types


def _ask(question: questionary.Question) -> Any:
  """Run a questionary prompt's .ask() in a fresh thread with its own event
  loop.

  ``_prompt_for_function_call`` is called synchronously (not awaited) from
  inside ADK's already-running asyncio event loop. questionary/prompt_toolkit's
  .ask() calls asyncio.run() internally, which raises "asyncio.run() cannot
  be called from a running event loop" in that situation. Running it on a
  separate thread gives it a loop-free thread to create its own loop in.
  """
  result: dict[str, Any] = {}

  def _worker() -> None:
    result["value"] = question.ask()

  worker = threading.Thread(target=_worker)
  worker.start()
  worker.join()
  return result.get("value")


def _extract_choices(schema: Any, payload: Any) -> tuple[list[Any] | None, bool]:
  """Returns (choices, is_multi) or (None, False) if no choices are given."""
  is_multi = isinstance(schema, dict) and schema.get("type") == "array"

  if isinstance(schema, dict):
    if "enum" in schema:
      return schema["enum"], is_multi
    items = schema.get("items")
    if isinstance(items, dict) and "enum" in items:
      return items["enum"], True

  if isinstance(payload, dict):
    choices = payload.get("choices") or payload.get("options")
    if choices:
      return list(choices), is_multi

  return None, is_multi


def _prompt_for_function_call_select(
    fc_id: str, fc_name: str, args: dict[str, Any]
) -> types.Content:
  """questionary-based replacement for adk_cli._prompt_for_function_call."""
  if fc_name == adk_cli._REQUEST_CONFIRMATION:
    tool_confirmation = args.get("toolConfirmation", {})
    hint = tool_confirmation.get("hint", "")
    original_fc = args.get("originalFunctionCall", {})
    original_name = original_fc.get("name", "unknown")
    confirmed = _ask(
        questionary.confirm(hint or f"Confirm {original_name}?", default=False)
    )
    response: dict[str, Any] = {"confirmed": bool(confirmed)}

  elif fc_name == adk_cli._REQUEST_INPUT:
    message = args.get("message") or "Input requested"
    schema = args.get("response_schema")
    payload = args.get("payload")
    choices, is_multi = _extract_choices(schema, payload)

    if choices and is_multi:
      result = _ask(questionary.checkbox(message, choices=choices))
    elif choices:
      result = _ask(questionary.select(message, choices=choices))
    else:
      raw = _ask(questionary.text(message))
      if raw is None:
        # User cancelled (Ctrl+C/Esc) -- fall back to an empty string so
        # downstream response_schema=str validation doesn't choke on None.
        raw = ""
      try:
        result = json.loads(raw)
      except (json.JSONDecodeError, ValueError, TypeError):
        result = raw

    response = result if isinstance(result, dict) else {"result": result}

  else:
    click.echo(f"[HITL] Waiting for input for {fc_name}({args})")
    raw = _ask(questionary.text("[user]: "))
    response = {"result": raw}

  return types.Content(
      role="user",
      parts=[
          types.Part(
              function_response=types.FunctionResponse(
                  id=fc_id,
                  name=fc_name,
                  response=response,
              )
          )
      ],
  )


def main() -> None:
  adk_cli._prompt_for_function_call = _prompt_for_function_call_select
  from google.adk.cli.cli_tools_click import cli_run

  # Re-dispatch straight into ADK's own `run` command with our patch
  # already applied, so all of its flags (--replay, --resume, --jsonl,
  # --save_session, ...) keep working unmodified.
  cli_run.main(args=sys.argv[1:], prog_name="run_cli_select.py")


if __name__ == "__main__":
  main()
