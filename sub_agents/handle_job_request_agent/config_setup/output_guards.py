import json
import re
import time

from google.adk.agents.context import Context
from google.adk.events import Event
from google.genai import types

from .composio_client import COMPOSIO_USER_ID, REQUIRED_TOOLKITS, composio_client


def guard_structured_output(state_key: str):
    """after_model_callback that catches a final response that isn't valid
    JSON before pydantic's output_schema validation blows up with an opaque
    traceback. This happens when a Composio tool call doesn't return the
    expected data and instead returns plain text (e.g. OneDrive's "please
    configure your OneDrive" message when the account's OneDrive hasn't been
    provisioned yet) -- the model just relays that text, which isn't valid
    JSON.

    The raw text is stashed in ctx.state[f"{state_key}_error"] and the
    response is swapped for an empty JSON array so schema validation
    succeeds; a downstream `raise_if_tool_error` node then surfaces the
    stashed text as a clean error instead of a stack trace."""

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
                parts=[types.Part(text="[]")],
            )
            return llm_response.model_copy(update={"content": new_content})
        return None

    return _callback


REAUTH_URL_RE = re.compile(r"https://connect\.composio\.dev/link/[\w-]+")
REAUTH_TOOLKIT_RE = re.compile(r"authenticate your ([\w ]+?) account", re.IGNORECASE)

TOOLKIT_NAME_TO_SLUG = {
    "excel": "excel",
    "onedrive": "one_drive",
    "one drive": "one_drive",
}


def _parse_reauth_prompt(text: str) -> tuple[str, str] | None:
    """Composio's MCP layer can return a just-in-time "please authenticate"
    prompt from a tool call even when `connected_accounts.list` already shows
    the toolkit ACTIVE (e.g. a scope needing separate re-consent). If `text`
    is one of those prompts, extract the toolkit name and reconnect URL."""
    url_match = REAUTH_URL_RE.search(text)
    if not url_match:
        return None
    toolkit_match = REAUTH_TOOLKIT_RE.search(text)
    toolkit_name = toolkit_match.group(1).strip() if toolkit_match else "the"
    return toolkit_name, url_match.group(0)


def _wait_for_toolkit_active(
    toolkit_name: str, timeout: float = 300, interval: float = 3.0
) -> bool:
    """Poll connected_accounts until `toolkit_name` (or, if unrecognized, any
    required toolkit) shows ACTIVE, or the timeout elapses."""
    toolkit_slug = TOOLKIT_NAME_TO_SLUG.get(toolkit_name.strip().lower())
    toolkit_slugs = [toolkit_slug] if toolkit_slug else REQUIRED_TOOLKITS
    deadline = time.time() + timeout
    while time.time() < deadline:
        existing = composio_client.connected_accounts.list(
            user_ids=[COMPOSIO_USER_ID],  # type: ignore[reportArgumentType]
            toolkit_slugs=toolkit_slugs,
            statuses=["ACTIVE"],
        )
        if existing.items:
            return True
        time.sleep(interval)
    return False


def raise_if_tool_error(state_key: str, hint: str = ""):
    """Check node to insert right after a `retrieve_*` agent. If
    `guard_structured_output` caught a non-JSON response for `state_key`:
    - if it's Composio's just-in-time reauth prompt, surface the link, wait
      for the user to reconnect, and route "retry" back to the retrieve step.
    - otherwise, raise a clear error instead of silently continuing with an
      empty list.
    On success, routes "ok" with the node input unchanged."""

    def _check(node_input, ctx: Context):
        error_text = ctx.state.get(f"{state_key}_error")
        if error_text:
            reauth = _parse_reauth_prompt(error_text)
            if reauth:
                toolkit_name, url = reauth
                yield Event(
                    message=(  # type: ignore[reportCallIssue]
                        f"Your {toolkit_name} connection needs to be re-authorized. "
                        f"Please click the link, then I'll continue automatically: {url}"
                    )
                )
                reconnected = _wait_for_toolkit_active(toolkit_name)
                ctx.state[f"{state_key}_error"] = None
                if reconnected:
                    yield Event(message="Reconnected, retrying...")  # type: ignore[reportCallIssue]
                    yield Event(route="retry", output=node_input)  # type: ignore[reportCallIssue]
                    return
                raise RuntimeError(f"Timed out waiting for {toolkit_name} reauthorization.")

            message = f"Expected structured data for '{state_key}' but got:\n{error_text}"
            if hint:
                message += f"\n\n{hint}"
            raise RuntimeError(message)
        yield Event(route="ok", output=node_input)  # type: ignore[reportCallIssue]

    _check.__name__ = f"raise_if_tool_error_{state_key}"
    return _check


def require_tool_before_reply(tool_name: str):
    """before_model_callback that forces the model to call `tool_name` on its
    first turn, instead of letting it skip straight to a (possibly non-JSON)
    text reply. Once a function call has been made in this turn, the model is
    left free to respond normally so it can still finalize its structured
    output_schema answer."""

    def _callback(callback_context, llm_request):
        already_called_tool = any(
            part.function_call is not None
            for content in (llm_request.contents or [])
            for part in (content.parts or [])
        )
        if not already_called_tool:
            llm_request.config.tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=[tool_name],
                )
            )
        return None

    return _callback
