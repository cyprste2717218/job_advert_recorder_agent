import json
from pathlib import Path

from google.adk.agents.context import Context
from google.adk.events import Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

REQUIRED_FIELDS = {"spreadsheet_id", "worksheet_name", "working_dir"}


def config_check_present_check():
    """Return an Event output on whether config.json exists with the required fields.

    Shared by response_job_workflow (workflow.py) and this module's own
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
    response to the parent Workflow (response_job_workflow)"""

    route = "False" if not node_input else "True"

    if route == "True":
        message = "All good, config is present."
    else:
        message = "Config is missing required fields."

    yield Event(message=message)  # type: ignore[reportCallIssue]
    yield Event(output=route)


def config_check_router(node_input: str):
    return Event(route=node_input)  # type: ignore[reportCallIssue]


def write_config_file(ctx: Context):
    """Write the selected folder/workbook/sheet/headers to config.json."""
    workbook_path = ctx.state.get("selected_workbook_path", "")
    working_dir = workbook_path.rsplit("/", 1)[0] if "/" in workbook_path else ""

    config = {
        "spreadsheet_id": ctx.state.get("selected_workbook_id"),
        "worksheet_name": ctx.state.get("selected_sheet_name"),
        "working_dir": working_dir,
        "drive_id": ctx.state.get("selected_drive_id"),
        "folder_path": ctx.state.get("selected_folder_path", ""),
        "sheet_headers": ctx.state.get("sheet_headers", []),
        "header_clarifications": ctx.state.get("header_clarifications", {}),
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2))

    # Downstream nodes (e.g. job_url_fetch_node) read "working_dir"
    # regardless of whether config came from this setup flow or was loaded
    # from an existing config.json (see load_config_into_context), so stash
    # it under the same key here too.
    ctx.state["working_dir"] = working_dir

    yield Event(message="Saved workbook/sheet configuration to config.json.")  # type: ignore[reportCallIssue]
    yield Event(output=config)
