import json
import os
import re
import time
from pathlib import Path

from composio import Composio
from dotenv import load_dotenv
from google.adk import Workflow
from google.adk.agents.context import Context
from google.adk.agents.llm_agent import Agent
from google.adk.events import Event, RequestInput
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.genai import types

from models.schemas import FolderItem, NamedItem, WorkbookItem

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

REQUIRED_FIELDS = {"spreadsheet_id", "worksheet_name", "working_dir"}

load_dotenv()


def config_check_present_check():
    """Return an Event output on whether config.json exists with the required fields.

    Shared by response_job_agent (agent.py) and this module's own
    post-write verification step; import from here rather than redefining."""

    if not CONFIG_PATH.is_file():
        return Event(output=False)

    try:
        data = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError:
        return Event(output=False)

    return Event(output=REQUIRED_FIELDS.issubset(data.keys()))


def checking_config_check_result(node_input: bool):
    """Update the user on the result of the config check and forward the
    response to the parent Workflow (response_job_agent)"""

    route = "False" if not node_input else "True"

    if route == "True":
        message = "All good, config is present."
    else:
        message = "Config is missing required fields."

    yield Event(message=message)  # type: ignore[reportCallIssue]
    yield Event(output=route)


def config_check_router(node_input: str):
    return Event(route=node_input)  # type: ignore[reportCallIssue]


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


MODEL = "gemini-3.1-flash-lite"

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID")

composio_client = Composio(api_key=COMPOSIO_API_KEY)  # type: ignore[reportArgumentType]

_session = composio_client.sessions.create(
    user_id=COMPOSIO_USER_ID,  # type: ignore[reportArgumentType]
    toolkits=["excel", "one_drive"],
    tools={
        "excel": {
            "enable": [
                "EXCEL_LIST_FILES",
                "EXCEL_LIST_WORKSHEETS",
                "EXCEL_GET_WORKSHEET_USED_RANGE",
            ]
        },
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


REQUIRED_TOOLKITS = ["one_drive", "excel"]


def ensure_composio_connections(ctx: Context):
    """Verify OneDrive/Excel are connected for this Composio user; if not, surface
    the OAuth link and wait for the user to complete it before continuing."""
    existing = composio_client.connected_accounts.list(
        user_ids=[COMPOSIO_USER_ID],  # type: ignore[reportArgumentType]
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
            user_id=COMPOSIO_USER_ID,  # type: ignore[reportArgumentType]
            auth_config_id=auth_config_id,
        )
        yield Event(
            message=(  # type: ignore[reportCallIssue]
                f"Please connect your {toolkit} account to continue: "
                f"{connection_request.redirect_url}"
            )
        )
        connection_request.wait_for_connection(timeout=300)

    yield Event(output=None)


def write_config_file(ctx: Context):
    """Write the selected folder/workbook/sheet/headers to config.json."""
    import json
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent.parent
    config_path = project_root / "config.json"

    workbook_path = ctx.state.get("selected_workbook_path", "")
    working_dir = workbook_path.rsplit("/", 1)[0] if "/" in workbook_path else ""

    config = {
        "spreadsheet_id": ctx.state.get("selected_workbook_id"),
        "worksheet_name": ctx.state.get("selected_sheet_name"),
        "working_dir": working_dir,
        "drive_id": ctx.state.get("selected_drive_id"),
        "folder_path": ctx.state.get("selected_folder_path", ""),
        "sheet_headers": ctx.state.get("sheet_headers", []),
    }
    config_path.write_text(json.dumps(config, indent=2))

    # Downstream nodes (e.g. response_job_url_fetch_node) read "working_dir"
    # regardless of whether config came from this setup flow or was loaded
    # from an existing config.json (see load_config_into_context), so stash
    # it under the same key here too.
    ctx.state["working_dir"] = working_dir

    yield Event(message="Saved workbook/sheet configuration to config.json.")  # type: ignore[reportCallIssue]
    yield Event(output=config)


def user_input_sheet(node_input):
    choices = [s.get("name", "unknown") for s in node_input]
    yield RequestInput(
        message="Which sheet?",
        response_schema=str,
        payload={"choices": choices},
    )


def user_input_workbook(node_input):
    choices = [f"{w.get('name', 'unknown')} ({w.get('path', 'unknown')})" for w in node_input]
    yield RequestInput(
        message="Choose a workbook",
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
    """Resolve the user-picked drive name to an id and stash it in state.

    Also resets folder navigation back to the drive root, since a freshly
    selected drive has no relationship to whatever folder was being browsed
    for a previously selected drive (relevant on a retry loop back to
    node 1)."""
    drives = ctx.state.get("onedrive_drives", [])
    drive = next((d for d in drives if d.get("name") == node_input), None)
    ctx.state["selected_drive_id"] = drive["id"] if drive else None
    ctx.state["current_folder_path"] = "/"

    yield Event(output=node_input)


FOLDER_NAV_SELECT_CHOICE = "Select this folder"
FOLDER_NAV_UP_CHOICE = ".. (up one level)"


def user_input_folder_navigation(node_input, ctx: Context):
    """Present the current folder's subfolders plus "up a level" and "select
    this folder" options, so the user can walk the drive's folder tree
    rather than being limited to a flat, drive-wide search."""
    current_path = ctx.state.get("current_folder_path", "/")
    folder_names = [f.get("name", "unknown") for f in node_input if f.get("is_folder", True)]

    choices = [f"{FOLDER_NAV_SELECT_CHOICE} ({current_path})"]
    if current_path != "/":
        choices.append(FOLDER_NAV_UP_CHOICE)
    choices.extend(folder_names)

    yield RequestInput(
        message=f"Browsing {current_path} -- pick a subfolder to open it, "
        "go up a level, or select the current folder to scope the "
        "workbook search to it:",
        response_schema=str,
        payload={"choices": choices},
    )


def resolve_folder_navigation(node_input: str, ctx: Context):
    """Apply the user's folder-navigation choice: descend into a subfolder,
    go up one level, or confirm the current folder as the workbook search
    scope. Folder paths are tracked as a plain string in ctx.state rather
    than resolved via a parent-lookup API call -- since navigation only
    ever moves relative to a path this node already knows, going up is
    just string manipulation."""
    current_path = ctx.state.get("current_folder_path", "/")

    if node_input.startswith(FOLDER_NAV_SELECT_CHOICE):
        ctx.state["selected_folder_path"] = "" if current_path == "/" else current_path
        yield Event(route="select", output=node_input)  # type: ignore[reportCallIssue]
        return

    if node_input == FOLDER_NAV_UP_CHOICE:
        parent_path = current_path.rsplit("/", 1)[0]
        ctx.state["current_folder_path"] = parent_path or "/"
        yield Event(route="navigate", output=node_input)  # type: ignore[reportCallIssue]
        return

    if current_path == "/":
        ctx.state["current_folder_path"] = f"/{node_input}"
    else:
        ctx.state["current_folder_path"] = f"{current_path}/{node_input}"
    yield Event(route="navigate", output=node_input)  # type: ignore[reportCallIssue]


def resolve_workbook_selection(node_input: str, ctx: Context):
    """Resolve the user-picked "name (path)" choice to a workbook and stash
    its id/name/path in state."""
    workbooks = ctx.state.get("onedrive_workbooks", [])
    workbook = next(
        (
            w
            for w in workbooks
            if f"{w.get('name', 'unknown')} ({w.get('path', 'unknown')})" == node_input
        ),
        None,
    )
    ctx.state["selected_workbook_id"] = workbook["id"] if workbook else None
    ctx.state["selected_workbook_name"] = workbook["name"] if workbook else node_input
    ctx.state["selected_workbook_path"] = workbook["path"] if workbook else ""

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


retrieve_folder_children = Agent(
    name="retrieve_folder_children",
    model=MODEL,
    mode="single_turn",
    tools=[composio_toolset],
    output_schema=list[FolderItem],
    output_key="folder_children",
    before_model_callback=require_tool_before_reply("ONE_DRIVE_LIST_FOLDER_CHILDREN"),
    after_model_callback=guard_structured_output("folder_children"),
    instruction="""
    You have access to Composio tools via MCP.
    CRITICAL: For the user's request, you MUST exclusively call the
    'ONE_DRIVE_LIST_FOLDER_CHILDREN' tool.
    Do not attempt to answer using general knowledge or seek other tools.
    Always prioritize tool execution as your very first step.

    Call the tool with drive_id={selected_drive_id} and
    folder_path={current_folder_path}.

    After the tool returns, respond with only the child items that are
    folders (ignore files), as id/name pairs with is_folder set to true.
    """,
)


retrieve_workbooks = Agent(
    name="retrieve_workbooks",
    model=MODEL,
    mode="single_turn",
    tools=[composio_toolset],
    output_schema=list[WorkbookItem],
    output_key="onedrive_workbooks",
    before_model_callback=require_tool_before_reply("EXCEL_LIST_FILES"),
    after_model_callback=guard_structured_output("onedrive_workbooks"),
    instruction="""
    You have access to Composio tools via MCP.
    CRITICAL: For the user's request, you MUST exclusively call the 'EXCEL_LIST_FILES' tool.
    Do not attempt to answer using general knowledge or seek other tools.
    Always prioritize tool execution as your very first step.

    Call the tool with drive_id={selected_drive_id}. If
    {selected_folder_path?} is non-empty, also pass it as path so the
    search is scoped to that folder (and its subfolders); if it's empty,
    omit path and search the whole drive recursively from the root.

    After the tool returns, respond with only the files whose name ends in
    .xlsx, .xlsm, or .xls, as id/name/path triples, where path is the file's
    full path within the drive (e.g. "/Documents/Jobs/Tracker.xlsx").
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
    CRITICAL: For the user's request, you MUST exclusively call the
    'EXCEL_GET_WORKSHEET_USED_RANGE' tool.
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
check_folder_children = raise_if_tool_error("folder_children")
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
            retrieve_folder_children,
            check_folder_children,
        ),
        (
            check_folder_children,
            {"retry": retrieve_folder_children, "ok": user_input_folder_navigation},
        ),
        (user_input_folder_navigation, resolve_folder_navigation),
        (
            resolve_folder_navigation,
            {"navigate": retrieve_folder_children, "select": retrieve_workbooks},
        ),
        (retrieve_workbooks, check_onedrive_workbooks),
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
        (write_config_file, config_check_present_check, checking_config_check_result),
    ],
)
