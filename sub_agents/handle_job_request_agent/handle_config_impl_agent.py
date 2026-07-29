import json
import os
import re
import time

from google.adk import Workflow
from google.adk.agents.context import Context
from google.adk.agents.llm_agent import Agent
from google.adk.events import Event, RequestInput
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from composio import Composio
from dotenv import load_dotenv
from google.genai import types
from pydantic import BaseModel

load_dotenv()


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
        except (json.JSONDecodeError, ValueError):
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


def _wait_for_toolkit_active(toolkit_name: str, timeout: float = 300, interval: float = 3.0) -> bool:
    """Poll connected_accounts until `toolkit_name` (or, if unrecognized, any
    required toolkit) shows ACTIVE, or the timeout elapses."""
    toolkit_slug = TOOLKIT_NAME_TO_SLUG.get(toolkit_name.strip().lower())
    toolkit_slugs = [toolkit_slug] if toolkit_slug else REQUIRED_TOOLKITS
    deadline = time.time() + timeout
    while time.time() < deadline:
        existing = composio_client.connected_accounts.list(
            user_ids=[COMPOSIO_USER_ID],
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
                    message=(
                        f"Your {toolkit_name} connection needs to be re-authorized. "
                        f"Please click the link, then I'll continue automatically: {url}"
                    )
                )
                reconnected = _wait_for_toolkit_active(toolkit_name)
                ctx.state.pop(f"{state_key}_error", None)
                if reconnected:
                    yield Event(message="Reconnected, retrying...")
                    yield Event(route="retry", output=node_input)
                    return
                raise RuntimeError(
                    f"Timed out waiting for {toolkit_name} reauthorization."
                )

            message = f"Expected structured data for '{state_key}' but got:\n{error_text}"
            if hint:
                message += f"\n\n{hint}"
            raise RuntimeError(message)
        yield Event(route="ok", output=node_input)

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

MODEL = "gemini-3.1-flash-lite"

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID")

composio_client = Composio(api_key=COMPOSIO_API_KEY)

_session = composio_client.sessions.create(
    user_id=COMPOSIO_USER_ID,
    toolkits=["excel", "one_drive"],
    tools={
        "excel": {"enable": ["EXCEL_LIST_FILES", "EXCEL_LIST_WORKSHEETS", "EXCEL_GET_WORKSHEET_USED_RANGE"]},
        "one_drive": {"enable": ["ONE_DRIVE_LIST_DRIVES", "ONE_DRIVE_LIST_FOLDER_CHILDREN"]},
    },
    preload={
        "tools": [
            "EXCEL_LIST_FILES",
            "EXCEL_LIST_WORKSHEETS",
            "EXCEL_GET_WORKSHEET_USED_RANGE",
            "ONE_DRIVE_LIST_DRIVES",
            "ONE_DRIVE_LIST_FOLDER_CHILDREN",
        ]
    },
    mcp=True,
)

composio_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=_session.mcp.url,
        headers={k: v for k, v in (_session.mcp.headers or {}).items() if v is not None},
    ),
)


class NamedItem(BaseModel):
    id: str
    name: str


REQUIRED_TOOLKITS = ["one_drive", "excel"]


def ensure_composio_connections(ctx: Context):
    """Verify OneDrive/Excel are connected for this Composio user; if not, surface
    the OAuth link and wait for the user to complete it before continuing."""
    existing = composio_client.connected_accounts.list(
        user_ids=[COMPOSIO_USER_ID],
        toolkit_slugs=REQUIRED_TOOLKITS,
        statuses=["ACTIVE"],
    )
    connected_slugs = {item.toolkit.slug.lower() for item in existing.items}

    for toolkit in REQUIRED_TOOLKITS:
        if toolkit in connected_slugs:
            continue

        # `toolkits.authorize()` calls the legacy `connected_accounts.initiate()`
        # endpoint, which Composio has retired for Composio-managed OAuth. Use
        # `connected_accounts.link()` instead, resolving the auth config id the
        # same way `authorize()` does internally.
        auth_config_id = composio_client.toolkits._get_auth_config_id(toolkit=toolkit)
        connection_request = composio_client.connected_accounts.link(
            user_id=COMPOSIO_USER_ID,
            auth_config_id=auth_config_id,
        )
        yield Event(
            message=(
                f"Please connect your {toolkit} account to continue: "
                f"{connection_request.redirect_url}"
            )
        )
        connection_request.wait_for_connection(timeout=300)

    yield Event(output=None)


def write_config_file(ctx: Context):
    """Write the selected folder/workbook/sheet/headers to config.json."""
    from pathlib import Path
    import json

    project_root = Path(__file__).resolve().parent.parent.parent
    config_path = project_root / "config.json"

    config = {
        "spreadsheet_id": ctx.state.get("selected_workbook_id"),
        "worksheet_name": ctx.state.get("selected_sheet_name"),
        "working_dir": ctx.state.get("selected_folder_path"),
        "drive_id": ctx.state.get("selected_drive_id"),
        "sheet_headers": ctx.state.get("sheet_headers", []),
    }
    config_path.write_text(json.dumps(config, indent=2))

    yield Event(message="Saved folder/workbook/sheet configuration to config.json.")
    yield Event(output=config)


def user_input_sheet(node_input):
    choices = [s.get("name", "unknown") for s in node_input]
    yield RequestInput(
        message="Which sheet?",
        response_schema=str,
        payload={"choices": choices},
        )


def user_input_workbook(node_input):
    choices = [w.get("name", "unknown") for w in node_input]
    yield RequestInput(
        message="Choose a workbook",
        response_schema=str,
        payload={"choices": choices},
        )


def user_input_folder(node_input):
    choices = [f.get("name", "unknown") for f in node_input]
    yield RequestInput(
        message="What folder in your OneDrive should I look in?",
        response_schema=str,
        payload={"choices": choices},
        )


def user_input_drive(node_input):
    choices = [d.get("name", d.get("id", "unknown")) for d in node_input]
    yield RequestInput(
        message="What OneDrive drive do you want to use?",
        response_schema=str,
        payload={"choices": choices},
        )


def resolve_drive_selection(node_input: str, ctx: Context):
    """Resolve the user-picked drive name to an id and stash it in state."""
    drives = ctx.state.get("onedrive_drives", [])
    drive = next((d for d in drives if d.get("name") == node_input), None)
    ctx.state["selected_drive_id"] = drive["id"] if drive else None

    yield Event(output=node_input)


def resolve_folder_selection(node_input: str, ctx: Context):
    """Stash the user-picked folder path in state."""
    ctx.state["selected_folder_path"] = node_input

    yield Event(output=node_input)


def resolve_workbook_selection(node_input: str, ctx: Context):
    """Resolve the user-picked workbook name to an id and stash it in state."""
    workbooks = ctx.state.get("onedrive_workbooks", [])
    workbook = next((w for w in workbooks if w.get("name") == node_input), None)
    ctx.state["selected_workbook_id"] = workbook["id"] if workbook else None
    ctx.state["selected_workbook_name"] = node_input

    yield Event(output=node_input)


def resolve_sheet_selection(node_input: str, ctx: Context):
    """Stash the user-picked sheet name in state."""
    ctx.state["selected_sheet_name"] = node_input

    yield Event(output=node_input)


retrieve_onedrive_drives = Agent(
    name="retrieve_onedrive_drives",
    model=MODEL,
    mode="single_turn",
    tools=[composio_toolset],
    output_schema=list[NamedItem],
    output_key="onedrive_drives",
    before_model_callback=require_tool_before_reply("ONE_DRIVE_LIST_DRIVES"),
    after_model_callback=guard_structured_output("onedrive_drives"),
    instruction="""
    You have access to Composio tools via MCP.
    CRITICAL: For the user's request, you MUST exclusively call the 'ONE_DRIVE_LIST_DRIVES' tool.
    Do not attempt to answer using general knowledge or seek other tools.
    Always prioritize tool execution as your very first step.
    After the tool returns, respond with the list of drives as id/name pairs.
    """,
)


retrieve_folders = Agent(
    name="retrieve_folders",
    model=MODEL,
    mode="single_turn",
    tools=[composio_toolset],
    output_schema=list[NamedItem],
    output_key="onedrive_folders",
    before_model_callback=require_tool_before_reply("ONE_DRIVE_LIST_FOLDER_CHILDREN"),
    after_model_callback=guard_structured_output("onedrive_folders"),
    instruction="""
    You have access to Composio tools via MCP.
    CRITICAL: For the user's request, you MUST exclusively call the 'ONE_DRIVE_LIST_FOLDER_CHILDREN' tool.
    Do not attempt to answer using general knowledge or seek other tools.
    Always prioritize tool execution as your very first step.

    Call the tool with drive_id={selected_drive_id}. If selected_drive_id is
    None, call the tool with use_me_drive=True and no drive_id instead.

    After the tool returns, respond with only the entries that are folders, as
    id/name pairs.
    """,
)


retrieve_workbooks = Agent(
    name="retrieve_workbooks",
    model=MODEL,
    mode="single_turn",
    tools=[composio_toolset],
    output_schema=list[NamedItem],
    output_key="onedrive_workbooks",
    before_model_callback=require_tool_before_reply("EXCEL_LIST_FILES"),
    after_model_callback=guard_structured_output("onedrive_workbooks"),
    instruction="""
    You have access to Composio tools via MCP.
    CRITICAL: For the user's request, you MUST exclusively call the 'EXCEL_LIST_FILES' tool.
    Do not attempt to answer using general knowledge or seek other tools.
    Always prioritize tool execution as your very first step.

    The user's message is the folder path to list files in. Call the tool
    with drive_id={selected_drive_id} and that folder path.

    After the tool returns, respond with only the files whose name ends in
    .xlsx, .xlsm, or .xls, as id/name pairs.
    """,
)


retrieve_sheets = Agent(
    name="retrieve_sheets",
    model=MODEL,
    mode="single_turn",
    tools=[composio_toolset],
    output_schema=list[NamedItem],
    output_key="workbook_sheets",
    before_model_callback=require_tool_before_reply("EXCEL_LIST_WORKSHEETS"),
    after_model_callback=guard_structured_output("workbook_sheets"),
    instruction="""
    You have access to Composio tools via MCP.
    CRITICAL: For the user's request, you MUST exclusively call the 'EXCEL_LIST_WORKSHEETS' tool.
    Do not attempt to answer using general knowledge or seek other tools.
    Always prioritize tool execution as your very first step.

    Call the tool with item_id={selected_workbook_id} and
    drive_id={selected_drive_id}.

    After the tool returns, respond with the list of sheets as id/name pairs.
    """,
)


retrieve_sheet_headers = Agent(
    name="retrieve_sheet_headers",
    model=MODEL,
    mode="single_turn",
    tools=[composio_toolset],
    output_schema=list[str],
    output_key="sheet_headers",
    before_model_callback=require_tool_before_reply("EXCEL_GET_WORKSHEET_USED_RANGE"),
    after_model_callback=guard_structured_output("sheet_headers"),
    instruction="""
    You have access to Composio tools via MCP.
    CRITICAL: For the user's request, you MUST exclusively call the 'EXCEL_GET_WORKSHEET_USED_RANGE' tool.
    Do not attempt to answer using general knowledge or seek other tools.
    Always prioritize tool execution as your very first step.

    The user's message names the worksheet to read headers from; use it as
    worksheet_id. Call the tool with item_id={selected_workbook_id},
    drive_id={selected_drive_id}, that worksheet_id, and values_only=True.

    After the tool returns, respond with only the first row of values (the
    column headers) as a list of strings.
    """,
)


check_onedrive_drives = raise_if_tool_error(
    "onedrive_drives",
    hint=(
        "Your OneDrive may not be provisioned yet. Open "
        "https://onedrive.live.com once with this Microsoft "
        "account to set it up, then try again."
    ),
)
check_onedrive_folders = raise_if_tool_error("onedrive_folders")
check_onedrive_workbooks = raise_if_tool_error("onedrive_workbooks")
check_workbook_sheets = raise_if_tool_error("workbook_sheets")
check_sheet_headers = raise_if_tool_error("sheet_headers")


response_handle_config_impl_node = Workflow(
    name="response_handle_config_impl_node",
    edges=[
        (
            "START",
            ensure_composio_connections,
            retrieve_onedrive_drives,
            check_onedrive_drives,
        ),
        (check_onedrive_drives, {"retry": retrieve_onedrive_drives, "ok": user_input_drive}),
        (
            user_input_drive,
            resolve_drive_selection,
            retrieve_folders,
            check_onedrive_folders,
        ),
        (check_onedrive_folders, {"retry": retrieve_folders, "ok": user_input_folder}),
        (
            user_input_folder,
            resolve_folder_selection,
            retrieve_workbooks,
            check_onedrive_workbooks,
        ),
        (check_onedrive_workbooks, {"retry": retrieve_workbooks, "ok": user_input_workbook}),
        (
            user_input_workbook,
            resolve_workbook_selection,
            retrieve_sheets,
            check_workbook_sheets,
        ),
        (check_workbook_sheets, {"retry": retrieve_sheets, "ok": user_input_sheet}),
        (
            user_input_sheet,
            resolve_sheet_selection,
            retrieve_sheet_headers,
            check_sheet_headers,
        ),
        (check_sheet_headers, {"retry": retrieve_sheet_headers, "ok": write_config_file}),
    ],
)
