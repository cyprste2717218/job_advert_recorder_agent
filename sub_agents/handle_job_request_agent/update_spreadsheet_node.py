import json
import os

from google.adk import Workflow
from google.adk.agents.context import Context
from google.adk.agents.llm_agent import Agent
from google.adk.events import Event
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from composio import Composio
from dotenv import load_dotenv
from google.genai import types
from pydantic import BaseModel

from models.schemas import SpreadsheetWriteResult

load_dotenv()

MODEL = "gemini-3.1-flash-lite"
MAX_WRITE_ATTEMPTS = 3

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID")

composio_client = Composio(api_key=COMPOSIO_API_KEY)

# Own Composio session/toolset (rather than importing the one from
# handle_config_impl_agent.py) to avoid a circular import: that module
# already imports response_job_url_fetch_node from job_url_fetch_agent.py,
# which in turn needs this module's node 14/15 to build its Workflow.
_session = composio_client.sessions.create(
    user_id=COMPOSIO_USER_ID,
    toolkits=["excel"],
    tools={
        "excel": {"enable": ["EXCEL_GET_WORKSHEET_USED_RANGE", "EXCEL_UPDATE_RANGE"]},
    },
    preload={"tools": ["EXCEL_GET_WORKSHEET_USED_RANGE", "EXCEL_UPDATE_RANGE"]},
    mcp=True,
)

composio_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=_session.mcp.url,
        headers={k: v for k, v in (_session.mcp.headers or {}).items() if v is not None},
    ),
)


def guard_structured_output(state_key: str):
    """after_model_callback that catches a final response that isn't valid
    JSON before pydantic's output_schema validation blows up with an opaque
    traceback. The raw text is stashed in ctx.state[f"{state_key}_error"]
    and the response is swapped for an empty JSON object so schema
    validation succeeds; check_spreadsheet_write then surfaces the stashed
    text as a clean error/retry instead of a stack trace.

    Mirrors job_url_fetch_agent.guard_structured_output; kept local here
    (rather than imported) for the same circular-import reason noted above."""

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
        except (json.JSONDecodeError, ValueError):
            callback_context.state[f"{state_key}_error"] = text
            new_content = types.Content(
                role=llm_response.content.role,
                parts=[types.Part(text="{}")],
            )
            return llm_response.model_copy(update={"content": new_content})
        return None

    return _callback


# Node 14 (agent): access the selected sheet and write the in-memory record
# as a new row via the Composio MCP server.
write_job_record_agent = Agent(
    model=MODEL,
    name="write_job_record_agent",
    description="Writes the extracted job posting record as a new row in the configured Excel worksheet via the Composio MCP server.",
    output_schema=SpreadsheetWriteResult,
    output_key="spreadsheet_write_result",
    after_model_callback=guard_structured_output("spreadsheet_write_result"),
    tools=[composio_toolset],
    instruction="""
    # Your Identity
    You are a meticulous spreadsheet operator.

    # Your Mission
    Write the extracted job record in {job_spec_details} as a new row at the
    bottom of the configured worksheet, with each value placed under its
    matching column header.

    # How You Work
    1. **Find the next empty row** - Call `EXCEL_GET_WORKSHEET_USED_RANGE` with
       item_id={selected_workbook_id}, drive_id={selected_drive_id},
       worksheet_id={selected_sheet_name}, values_only=True to find how many
       rows are currently used.
    2. **Build the row** - In the same left-to-right order as {sheet_headers},
       take each header's corresponding value from {job_spec_details}
       (use "" for any field that's missing).
    3. **Write the row** - Call `EXCEL_UPDATE_RANGE` with
       item_id={selected_workbook_id}, worksheet_id={selected_sheet_name},
       an address for the single row immediately below the used range
       (spanning the same number of columns as {sheet_headers}, e.g.
       "A5:D5"), and values=[[...]] containing the row you built.

    # Output Format
    Respond with only a JSON object of the form:
    {{"success": true or false, "row_address": "<address you wrote to>"}}
    Do not include any text outside the JSON object.
    """,
)


def raise_if_write_error(state_key: str, attempts_key: str):
    """Check node run immediately after write_job_record_agent. If
    guard_structured_output caught a non-JSON response for `state_key`,
    routes "retry" back to the agent (up to MAX_WRITE_ATTEMPTS, tracked
    under `attempts_key`); once attempts are exhausted it raises instead of
    silently telling the user the sheet was updated when it wasn't. On
    success, routes "ok" with the node input unchanged."""

    def _check(node_input, ctx: Context):
        error_text = ctx.state.get(f"{state_key}_error")
        ctx.state[f"{state_key}_error"] = None
        if error_text:
            attempts = ctx.state.get(attempts_key, 0) + 1
            ctx.state[attempts_key] = attempts
            if attempts < MAX_WRITE_ATTEMPTS:
                yield Event(message=f"Malformed response for '{state_key}', retrying...")
                yield Event(route="retry", output=node_input)
                return
            raise RuntimeError(
                f"Expected structured data for '{state_key}' after {attempts} "
                f"attempts but got:\n{error_text}"
            )
        ctx.state[attempts_key] = 0
        yield Event(route="ok", output=node_input)

    _check.__name__ = f"raise_if_write_error_{state_key}"
    return _check


check_spreadsheet_write = raise_if_write_error(
    "spreadsheet_write_result", "spreadsheet_write_attempts"
)


def tell_user_spreadsheet_updated(node_input, ctx: Context):
    """Node 15 (function): tell the user that the spreadsheet has been updated."""
    result = ctx.state.get("spreadsheet_write_result") or {}
    if isinstance(result, BaseModel):
        result = result.model_dump()

    if result.get("success"):
        row_address = result.get("row_address") or "the next available row"
        yield Event(message=f"Spreadsheet updated: new row written at {row_address}.")
    else:
        yield Event(message="Spreadsheet update reported failure; please check the worksheet.")

    yield Event(output=node_input)


response_update_spreadsheet_node = Workflow(
    name="response_update_spreadsheet_node",
    edges=[
        ("START", write_job_record_agent, check_spreadsheet_write),
        (check_spreadsheet_write, {"retry": write_job_record_agent, "ok": tell_user_spreadsheet_updated}),
    ],
)
