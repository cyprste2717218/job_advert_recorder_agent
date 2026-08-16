import json

from google.adk.agents.context import Context
from google.adk.events import Event
from google.genai import types

MAX_EXTRACTION_ATTEMPTS = 3


def guard_structured_output(state_key: str):
    """after_model_callback that catches a final response that isn't valid
    JSON before pydantic's output_schema validation blows up with an opaque
    traceback (e.g. the model explaining itself in prose instead of
    returning JSON). The raw text is stashed in
    ctx.state[f"{state_key}_error"] and the response is swapped for an
    empty JSON object so schema validation succeeds; a downstream
    raise_if_extraction_error node then surfaces the stashed text as a
    clean error/retry instead of a stack trace.

    Mirrors config_setup_workflow.output_guards.guard_structured_output; kept
    local here (rather than imported) to avoid a circular import, since
    config_setup_workflow/workflow.py already imports job_url_fetch_node from
    job_url_fetch_workflow/workflow.py."""

    def _callback(callback_context, llm_response):
        if llm_response.content is None:
            return None
        parts = llm_response.content.parts or []
        if any(part.function_call is not None for part in parts):
            return None  # still calling tools; nothing to validate yet

        text = "".join(part.text or "" for part in parts).strip()
        if not text:
            return None
        try:
            json.loads(text)
        except json.JSONDecodeError, ValueError:
            callback_context.state[f"{state_key}_error"] = text
            new_content = types.Content(
                role=llm_response.content.role,
                parts=[types.Part(text="{}")],
            )
            return llm_response.model_copy(update={"content": new_content})
        return None

    return _callback


def raise_if_extraction_error(
    state_key: str,
    attempts_key: str,
    success_message: str = "Extraction accepted, proceeding to verification...",
):
    """Check node run immediately after a structured-output agent. If
    guard_structured_output caught a non-JSON response for `state_key`,
    routes "retry" back to the agent (up to MAX_EXTRACTION_ATTEMPTS,
    tracked under `attempts_key`); once attempts are exhausted it raises
    instead of silently continuing with a garbage object. On success,
    routes "ok" with the node input unchanged.

    Simpler than config_setup_workflow.output_guards.raise_if_tool_error: no
    Composio reauth branch, since job-posting extraction has no Composio
    tools."""

    def _check(node_input, ctx: Context):
        error_text = ctx.state.get(f"{state_key}_error")
        ctx.state[f"{state_key}_error"] = None
        if error_text:
            attempts = ctx.state.get(attempts_key, 0) + 1
            ctx.state[attempts_key] = attempts
            if attempts < MAX_EXTRACTION_ATTEMPTS:
                yield Event(message=f"Malformed response for '{state_key}', retrying...")  # type: ignore[reportCallIssue]
                yield Event(route="retry", output=node_input)  # type: ignore[reportCallIssue]
                return
            raise RuntimeError(
                f"Expected structured data for '{state_key}' after {attempts} "
                f"attempts but got:\n{error_text}"
            )
        ctx.state[attempts_key] = 0
        yield Event(message=success_message)  # type: ignore[reportCallIssue]
        yield Event(route="ok", output=node_input)  # type: ignore[reportCallIssue]

    _check.__name__ = f"raise_if_extraction_error_{state_key}"
    return _check
