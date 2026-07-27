import os

from google.adk import Workflow
from google.adk.agents.context import Context
from google.adk.events import Event, RequestInput

from composio import Composio
from dotenv import load_dotenv

load_dotenv()

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID")

composio_client = Composio(api_key=COMPOSIO_API_KEY)


def _execute_composio_tool(slug: str, arguments: dict) -> dict:
    """Execute a Composio tool by slug and return its data payload."""
    response = composio_client.tools.execute(
        slug=slug,
        arguments=arguments,
        user_id=COMPOSIO_USER_ID,
    )
    if not response["successful"]:
        raise RuntimeError(f"Composio tool {slug} failed: {response['error']}")
    return response["data"]


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

def retrieve_onedrive_drives(ctx: Context):
    """List the OneDrive drives available to the authenticated user."""
    data = _execute_composio_tool("ONE_DRIVE_LIST_DRIVES", {})
    drives = data.get("value", [])
    ctx.state["onedrive_drives"] = drives

    names = ", ".join(d.get("name", d.get("id", "unknown")) for d in drives)
    yield Event(message=f"Available OneDrive drives: {names or 'none found'}")
    yield Event(output=drives)


def retrieve_folders(node_input: str, ctx: Context):
    """List the folders in the selected OneDrive drive."""
    drives = ctx.state.get("onedrive_drives", [])
    drive = next((d for d in drives if d.get("name") == node_input), None)
    drive_id = drive["id"] if drive else None
    ctx.state["selected_drive_id"] = drive_id

    data = _execute_composio_tool(
        "ONE_DRIVE_LIST_FOLDER_CHILDREN",
        {"drive_id": drive_id, "use_me_drive": drive_id is None},
    )
    children = data.get("value", [])
    folders = [child for child in children if "folder" in child]
    ctx.state["onedrive_folders"] = folders

    names = ", ".join(folder.get("name", "unknown") for folder in folders)
    yield Event(message=f"Folders in {node_input!r}: {names or 'none found'}")
    yield Event(output=folders)


def retrieve_workbooks(node_input: str, ctx: Context):
    """List the Excel workbooks in the selected folder."""
    ctx.state["selected_folder_path"] = node_input

    data = _execute_composio_tool(
        "EXCEL_LIST_FILES",
        {"drive_id": ctx.state.get("selected_drive_id"), "path": node_input},
    )
    files = data.get("value", [])
    workbooks = [
        f for f in files if f.get("name", "").lower().endswith((".xlsx", ".xlsm", ".xls"))
    ]
    ctx.state["onedrive_workbooks"] = workbooks

    names = ", ".join(w.get("name", "unknown") for w in workbooks)
    yield Event(message=f"Workbooks in {node_input!r}: {names or 'none found'}")
    yield Event(output=workbooks)


def retrieve_sheets(node_input: str, ctx: Context):
    """List the sheets within the selected workbook."""
    workbooks = ctx.state.get("onedrive_workbooks", [])
    workbook = next((w for w in workbooks if w.get("name") == node_input), None)
    ctx.state["selected_workbook_id"] = workbook["id"] if workbook else None
    ctx.state["selected_workbook_name"] = node_input

    data = _execute_composio_tool(
        "EXCEL_LIST_WORKSHEETS",
        {
            "item_id": ctx.state.get("selected_workbook_id"),
            "drive_id": ctx.state.get("selected_drive_id"),
        },
    )
    sheets = data.get("value", [])
    ctx.state["workbook_sheets"] = sheets

    names = ", ".join(sheet.get("name", "unknown") for sheet in sheets)
    yield Event(message=f"Sheets in {node_input!r}: {names or 'none found'}")
    yield Event(output=sheets)

def retrieve_sheet_headers(node_input: str, ctx: Context):
    """Retrieve the column headers of the selected sheet."""
    ctx.state["selected_sheet_name"] = node_input

    data = _execute_composio_tool(
        "EXCEL_GET_WORKSHEET_USED_RANGE",
        {
            "item_id": ctx.state.get("selected_workbook_id"),
            "drive_id": ctx.state.get("selected_drive_id"),
            "worksheet_id": node_input,
            "values_only": True,
        },
    )
    values = data.get("values", [])
    headers = values[0] if values else []
    ctx.state["sheet_headers"] = headers

    yield Event(message=f"Column headers in {node_input!r}: {', '.join(headers) or 'none found'}")
    yield Event(output=headers)


response_handle_config_impl_node = Workflow(
    name="response_handle_config_impl_node",
    edges=[
        (
            "START",
            retrieve_onedrive_drives,
            user_input_drive,
            retrieve_folders,
            user_input_folder,
            retrieve_workbooks,
            user_input_workbook,
            retrieve_sheets,
            user_input_sheet,
            retrieve_sheet_headers,
            write_config_file,
        ),
    ],
)
