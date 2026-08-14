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

import json
import sys
import threading
import warnings
from typing import Any

import questionary
from google.adk.cli import cli as adk_cli
from google.genai import types

# ADK warns (UserWarning) the first time each non-stable feature is touched,
# even for experimental features it ships default_on -- e.g. MCP toolset /
# authenticated-tool usage on the setup and write paths below trips this
# unconditionally. There's no ADK-native way to silence it (ADK_ENABLE_*/
# ADK_DISABLE_* only toggle the feature, not the warning), so filter it at
# the Python warnings-module level instead. These warnings fire from
# whichever module first touches the feature -- e.g.
# google.adk.features._feature_decorator (PLUGGABLE_AUTH), google.adk.cli.cli
# (InMemoryCredentialService construction), and
# google.adk.auth.credential_service.in_memory_credential_service
# (BaseCredentialService) all trip this on a normal `run_cli_select.py` run
# -- so match on the shared "[EXPERIMENTAL]" message prefix across all of
# google.adk rather than pinning to one emitting module.
warnings.filterwarnings(
    "ignore",
    message=r"^\[EXPERIMENTAL\]",
    category=UserWarning,
    module=r"google\.adk\..*",
)

# Light blue (bolded/italicised where it helps skimming) for everything,
# except success messages which get green -- currently just
# response_job_url_fetch_node's "Verification passed" milestone. Router/
# orchestration nodes (`response_*`) are italicised so the agents doing
# actual work stand out; everything else is upright.
_original_print_event: Any = None

# LLM `Agent` nodes -- as opposed to the plain function/generator Workflow
# nodes that narrate progress via `yield Event(message="...")`. ADK gives no
# way to tell the two apart on the Event itself (`message=` is just a
# construction-time convenience copied into `content`, not a retained flag),
# so this list is the only way to keep raw agent output -- which is just
# JSON matching each agent's output_schema, not human-readable prose -- out
# of the human-readable CLI.
_AGENT_NODE_AUTHORS = {
    "extract_job_spec_details_agent",
    "verify_job_spec_details_agent",
    "write_job_record_agent",
    "retrieve_onedrive_drives",
    "retrieve_folder_children",
    "retrieve_sheets",
    "retrieve_sheet_headers",
}


def _style_for_author(author: str) -> str:
    return "italic fg:lightblue" if author.startswith("response_") else "fg:lightblue"


def _decorate_job_url_fetch_text(text: str) -> tuple[str, bool]:
    """response_job_url_fetch_node narrates the extract -> verify loop; give
    its two milestone messages the requested icon and flag the success one
    (verification passed) so the caller can colour it green."""
    success_prefixes = (
        "Verification passed",
        "Job details succesfully fetched and updated in your workbook!",
    )
    if text.startswith("Extracting job details from:"):
        return f"🔍 {text}", False
    if text.startswith(success_prefixes):
        return f"{text} ✅", True
    return text, False


def _print_event_pretty(event: Any, jsonl: bool = False, session_id: Any = None) -> None:
    """questionary-based replacement for adk_cli._print_event.

    --jsonl output is for machine consumption, so it's passed straight
    through to the original implementation untouched; only the
    human-readable branch gets colour.
    """
    if jsonl:
        _original_print_event(event, jsonl=jsonl, session_id=session_id)
        return

    author = event.author or "unknown"
    if author in _AGENT_NODE_AUTHORS:
        return
    text_parts = (
        [p.text for p in event.content.parts if p.text]
        if event.content and event.content.parts
        else []
    )
    if text_parts:
        text = "".join(text_parts)
        is_success = False
        if author == "response_job_url_fetch_node":
            text, is_success = _decorate_job_url_fetch_text(text)
        if is_success:
            questionary.print(f"{author}: ", style="bold fg:green", end="")
            questionary.print(text, style="fg:green")
        else:
            questionary.print(f"{author}: ", style="bold fg:lightblue", end="")
            questionary.print(text, style=_style_for_author(author))
    elif event.long_running_tool_ids:
        questionary.print(f"{author}: (paused for input...)", style="bold fg:lightblue")


def _ask(question: questionary.Question) -> Any:
    """Run a questionary prompt's .ask() in a fresh thread with its own event
    loop.

    ``_prompt_for_function_call`` is called synchronously (not awaited) from
    inside ADK's already-running asyncio event loop. questionary/prompt_toolkit's
    .ask() calls asyncio.run() internally, which raises "asyncio.run() cannot
    be called from a running event loop" in that situation. Running it on a
    separate thread gives it a loop-free thread to create its own loop in.

    questionary swallows Ctrl+C/Esc internally and reports it by returning
    None from .ask() rather than raising -- so it never reaches this thread's
    caller as a KeyboardInterrupt. Treated here as "quit the CLI": routing a
    cancellation back through the Workflow graph as an ordinary route can't
    stop ADK's outer `run_interactively` loop (it only exits on a literal
    "exit" typed at its own top-level prompt), so instead this exits the
    process directly. That in turn unwinds through the exit and triggers
    browser_manager's atexit hook, so the Playwright/Chromium context still
    gets closed on the way out.
    """
    result: dict[str, Any] = {}

    def _worker() -> None:
        result["value"] = question.ask()

    worker = threading.Thread(target=_worker)
    worker.start()
    worker.join()

    if result.get("value") is None:
        questionary.print("\nCancelled (Ctrl+C) -- shutting down...", style="bold fg:lightblue")
        sys.exit(0)

    return result["value"]


def _truncate_display(text: str, max_len: int = 20) -> str:
    """Shortens a value for on-screen echo; the full value returned by .ask()
    is untouched -- questionary has no `transformer` kwarg (prompt_toolkit's
    PromptSession doesn't accept one), so callers erase questionary's own
    rendered line (`erase_when_done=True`) and reprint this truncated form
    themselves."""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


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
        confirmed = _ask(questionary.confirm(hint or f"Confirm {original_name}?", default=False))
        response: dict[str, Any] = {"confirmed": bool(confirmed)}

    elif fc_name == adk_cli._REQUEST_INPUT:
        message = args.get("message") or "Input requested"
        schema = args.get("response_schema")
        payload = args.get("payload")
        choices, is_multi = _extract_choices(schema, payload)
        is_bool = isinstance(schema, dict) and schema.get("type") == "boolean"

        if choices and is_multi:
            result = _ask(questionary.checkbox(message, choices=choices))
        elif choices:
            result = _ask(questionary.select(message, choices=choices))
        elif is_bool:
            result = _ask(questionary.confirm(message))
        else:
            is_job_url = "job url" in message.lower()
            qmark = "🔗" if is_job_url else "?"
            raw = _ask(questionary.text(message, qmark=qmark, erase_when_done=is_job_url))
            if is_job_url and raw:
                questionary.print(f"{message} {_truncate_display(raw)}", style="bold fg:lightblue")
            try:
                result = json.loads(raw)
            except json.JSONDecodeError, ValueError, TypeError:
                result = raw

        response = result if isinstance(result, dict) else {"result": result}

    else:
        questionary.print(f"[HITL] Waiting for input for {fc_name}({args})", style="fg:lightblue")
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
    global _original_print_event
    _original_print_event = adk_cli._print_event
    adk_cli._print_event = _print_event_pretty
    adk_cli._prompt_for_function_call = _prompt_for_function_call_select
    from google.adk.cli.cli_tools_click import cli_run

    # Re-dispatch straight into ADK's own `run` command with our patch
    # already applied, so all of its flags (--replay, --resume, --jsonl,
    # --save_session, ...) keep working unmodified.
    cli_run.main(args=sys.argv[1:], prog_name="run_cli_select.py")


if __name__ == "__main__":
    main()
