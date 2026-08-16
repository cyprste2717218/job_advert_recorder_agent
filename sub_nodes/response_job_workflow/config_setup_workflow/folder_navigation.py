from google.adk.agents.context import Context
from google.adk.events import Event, RequestInput


def user_input_sheet(node_input):
    choices = [s.get("name", "unknown") for s in node_input]
    yield RequestInput(
        message="Which sheet?",
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


FOLDER_NAV_UP_CHOICE = ".. (up one level)"


def user_input_folder_navigation(node_input, ctx: Context):
    """Present the current folder's subfolders and workbook files, plus an
    "up a level" option, so the user can walk the drive's folder tree and
    finalize their choice by picking a workbook directly rather than a
    separate folder-then-workbook selection step."""
    current_path = ctx.state.get("current_folder_path", "/")
    folder_names = [f.get("name", "unknown") for f in node_input if f.get("is_folder")]
    workbook_names = [f.get("name", "unknown") for f in node_input if not f.get("is_folder")]

    choices = []
    if current_path != "/":
        choices.append(FOLDER_NAV_UP_CHOICE)
    choices.extend(folder_names)
    choices.extend(workbook_names)

    yield RequestInput(
        message=f"Browsing {current_path} -- pick a subfolder to open it, "
        "go up a level, or pick a workbook to select it:",
        response_schema=str,
        payload={"choices": choices},
    )


def resolve_folder_navigation(node_input: str, ctx: Context):
    """Apply the user's folder-navigation choice: descend into a subfolder,
    go up one level, or confirm a workbook as the final selection. Folder
    paths are tracked as a plain string in ctx.state rather than resolved
    via a parent-lookup API call -- since navigation only ever moves
    relative to a path this node already knows, going up is just string
    manipulation."""
    current_path = ctx.state.get("current_folder_path", "/")

    if node_input == FOLDER_NAV_UP_CHOICE:
        parent_path = current_path.rsplit("/", 1)[0]
        ctx.state["current_folder_path"] = parent_path or "/"
        yield Event(route="navigate", output=node_input)  # type: ignore[reportCallIssue]
        return

    children = ctx.state.get("folder_children", [])
    item = next((f for f in children if f.get("name") == node_input), None)

    if item is not None and not item.get("is_folder"):
        ctx.state["selected_folder_path"] = "" if current_path == "/" else current_path
        ctx.state["selected_workbook_id"] = item["id"]
        ctx.state["selected_workbook_name"] = item["name"]
        ctx.state["selected_workbook_path"] = item.get("path", "")
        yield Event(route="select", output=node_input)  # type: ignore[reportCallIssue]
        return

    if current_path == "/":
        ctx.state["current_folder_path"] = f"/{node_input}"
    else:
        ctx.state["current_folder_path"] = f"{current_path}/{node_input}"
    yield Event(route="navigate", output=node_input)  # type: ignore[reportCallIssue]


def resolve_sheet_selection(node_input: str, ctx: Context):
    """Stash the user-picked sheet name in state."""
    ctx.state["selected_sheet_name"] = node_input

    yield Event(output=node_input)
