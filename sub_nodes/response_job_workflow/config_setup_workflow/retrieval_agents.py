import os

from google.adk.agents.llm_agent import Agent

from models.schemas import FolderItem, NamedItem

from .composio_client import composio_toolset
from .output_guards import guard_structured_output, raise_if_tool_error, require_tool_before_reply

MODEL = os.getenv("MODEL")


retrieve_onedrive_drives = Agent(
    name="retrieve_onedrive_drives",
    model=MODEL,  # type: ignore[reportArgumentType]
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

    To submit your final answer, call the 'set_model_response' tool exactly
    as spelled here (lowercase, with underscores) -- do not guess its name
    or casing from the other tool names above.
    """,
)


retrieve_folder_children = Agent(
    name="retrieve_folder_children",
    model=MODEL,  # type: ignore[reportArgumentType]
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

    After the tool returns, respond with the child items that are either
    folders, or workbook files whose name ends in .xlsx, .xlsm, or .xls
    (ignore all other file types). For folders, respond with id/name and
    is_folder set to true. For workbook files, respond with id/name,
    is_folder set to false, and path set to the file's full path within
    the drive (e.g. "/Documents/Jobs/Tracker.xlsx").

    To submit your final answer, call the 'set_model_response' tool exactly
    as spelled here (lowercase, with underscores) -- do not guess its name
    or casing from the other tool names above.
    """,
)


retrieve_sheets = Agent(
    name="retrieve_sheets",
    model=MODEL,  # type: ignore[reportArgumentType]
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

    To submit your final answer, call the 'set_model_response' tool exactly
    as spelled here (lowercase, with underscores) -- do not guess its name
    or casing from the other tool names above.
    """,
)


retrieve_sheet_headers = Agent(
    name="retrieve_sheet_headers",
    model=MODEL,  # type: ignore[reportArgumentType]
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

    To submit your final answer, call the 'set_model_response' tool exactly
    as spelled here (lowercase, with underscores) -- do not guess its name
    or casing from the other tool names above.
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
check_workbook_sheets = raise_if_tool_error("workbook_sheets")
check_sheet_headers = raise_if_tool_error("sheet_headers")
