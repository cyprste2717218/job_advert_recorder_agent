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
from rich.console import Console

from cli_display import StageDisplay

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

# Light blue (bolded/italicised where it helps skimming) for everything.
# Router/orchestration nodes (`response_*`) are italicised so the agents
# doing actual work stand out; everything else is upright. The
# config-presence check and the extract -> verify -> write pipeline are the
# exception -- their messages are absorbed into a `rich`-based live stage
# display instead (see `_handle_stage_a_message`/`_handle_stage_b_message`
# and cli_display.py) rather than printed as plain lines here.
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
    "detect_access_denied_agent",
    "verify_job_spec_details_agent",
    "write_job_record_agent",
    "retrieve_onedrive_drives",
    "retrieve_folder_children",
    "retrieve_sheets",
    "retrieve_sheet_headers",
}


def _style_for_author(author: str) -> str:
    return "italic fg:lightblue" if author.startswith("response_") else "fg:lightblue"


# rich Console shared by both live stage displays below; a single instance is
# required so they don't fight over cursor/terminal state.
_console = Console()

# Config-presence check (response_job_agent, github issue #20) and the
# extract -> verify -> write pipeline (response_job_url_fetch_node /
# response_update_spreadsheet_node, github issue #21) each narrate their
# progress purely through Event(message=...) text. These two module-level
# handles track whichever stage is currently live, matched by exact message
# text against the known strings those nodes emit (see handle_config_impl_agent.py
# / agent.py / job_url_fetch_agent.py / update_spreadsheet_node.py) -- there's
# no state flag on the Event itself to key off instead.
_stage_a: StageDisplay | None = None
_stage_b: StageDisplay | None = None

_STAGE_B_STEPS = ["Extracting job details", "Verifying details", "Updating spreadsheet"]

# Mirrors job_url_fetch_agent.ACCESS_BLOCKED_PREFIX -- kept as a hardcoded
# duplicate string rather than an import, matching how every other stage-b
# substep message is matched here by exact/prefix text rather than a shared
# constant (see _handle_stage_b_message).
_ACCESS_BLOCKED_PREFIX = "ACCESS_BLOCKED::"


def _handle_stage_a_message(author: str, text: str) -> bool:
    """response_job_agent's config-presence check: a permanent header while
    "Checking configuration"/"Loaded configuration" is underway, with each
    sub-message swapped in below it, collapsing to the outcome once the
    check (and, if config was present, the subsequent load) concludes.
    Returns True if this event was absorbed into the stage display."""
    global _stage_a

    if author != "response_job_agent":
        return False

    if text == "Checking if workbook/spreadsheet details configured yet...":
        _stage_a = StageDisplay(_console, steps=["Checking configuration"])
        _stage_a.start()
        _stage_a.set_substep(text)
        return True

    if _stage_a is None:
        return False

    if text == "Config is missing required fields.":
        _stage_a.set_substep(text)
        _stage_a.finish(text, icon="➡️", style="bold yellow")
        _stage_a = None
        return True

    if text == "All good, config is present.":
        _stage_a.set_substep(text)
        return True

    if text == "Loaded workbook/sheet configuration from config.json.":
        _stage_a.set_substep(text)
        _stage_a.finish(text, icon="✅", style="bold green")
        _stage_a = None
        return True

    if text == "Error loading configuration, trying again...":
        _stage_a.set_substep(text)
        return True

    if text.startswith("Unable to load config.json into context after"):
        _stage_a.set_substep(text)
        _stage_a.finish(text, icon="❌", style="bold red")
        _stage_a = None
        return True

    return False


def _handle_stage_b_message(author: str, text: str) -> bool:
    """The extract -> verify -> write pipeline: a permanent header showing
    the current macro-step next to a progress bar, with each sub-message
    swapped in below it, collapsing to the outcome once the whole pipeline
    concludes. Returns True if this event was absorbed into the stage
    display."""
    global _stage_b

    if author == "response_job_url_fetch_node" and text.startswith("Extracting job details from:"):
        _stage_b = StageDisplay(_console, steps=_STAGE_B_STEPS)
        _stage_b.start()
        _stage_b.set_substep(text)
        return True

    if _stage_b is None:
        return False

    if author == "response_job_url_fetch_node":
        if text == "Job details succesfully fetched and updated in your workbook!":
            _stage_b.set_substep(text)
            _stage_b.finish(text, icon="✅", style="bold green")
            _stage_b = None
            return True
        if text == "Extraction accepted, proceeding to verification...":
            _stage_b.set_step(1)
            _stage_b.set_substep(text)
            return True
        if text == "Verification passed, writing to spreadsheet...":
            _stage_b.set_step(2)
            _stage_b.set_substep(text)
            return True
        if text == "Verification found issues with the extracted details, retrying...":
            _stage_b.set_step(0)
            _stage_b.set_substep(text)
            return True
        if text.startswith("Malformed response for 'job_spec_"):
            _stage_b.set_substep(text)
            return True
        if text.startswith("Extracted so far ("):
            header, _, rest = text.partition("\n")
            lines = rest.split("\n") if rest else []
            _stage_b.set_substep_detail(header, lines)
            return True
        if text in (
            "Checking for access restrictions or auth walls...",
            "Access check parsed, evaluating result...",
            "No access restrictions detected, proceeding...",
        ):
            _stage_b.set_substep(text)
            return True
        if text.startswith(_ACCESS_BLOCKED_PREFIX):
            reason = text[len(_ACCESS_BLOCKED_PREFIX) :]
            title = f"Access denied -- {reason}"
            _stage_b.finish(title, icon="🚫", style="bold red")
            _stage_b = None
            return True
        return False

    if author == "response_update_spreadsheet_node":
        _stage_b.set_substep(text)
        return True

    return False


def _cleanup_live_stages() -> None:
    """Called from `main`'s finally block: if the graph raised out of a stage
    (e.g. verification/write attempts exhausted) instead of reaching that
    stage's success/failure message, stop its Live display rather than
    leaving an animated spinner behind for the traceback to print through."""
    global _stage_a, _stage_b
    if _stage_a is not None:
        _stage_a.abort()
        _stage_a = None
    if _stage_b is not None:
        _stage_b.abort()
        _stage_b = None


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
        if _handle_stage_a_message(author, text) or _handle_stage_b_message(author, text):
            return
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
    try:
        cli_run.main(args=sys.argv[1:], prog_name="run_cli_select.py")
    finally:
        # If a live stage display (see _handle_stage_a_message/
        # _handle_stage_b_message) was left running -- e.g. the graph raised
        # out of a retry loop instead of reaching that stage's terminal
        # message -- stop it so its animated spinner doesn't corrupt the
        # traceback click is about to print on the way out.
        _cleanup_live_stages()


if __name__ == "__main__":
    main()
