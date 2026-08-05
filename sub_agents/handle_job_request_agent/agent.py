import json
from pathlib import Path

from google.adk import Event, Workflow
from google.adk.agents.context import Context

from .handle_config_impl_agent import (
    config_check_present_check,
    response_handle_config_impl_node,
)
from .job_url_fetch_agent import (
    job_url_fetch_done,
    response_job_url_fetch_node,
)

# job_tracker_agent/ is the parent of sub_agents/, which is the parent of handle_job_request_agent/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"


def checking_config_check_result(node_input: bool):
    """Update the user on the result of the config check and forward the
    response to the event router"""

    route = "False" if not node_input else "True"

    if route == "True":
        message = "All good, config is present."
    else:
        message = "Config is missing required fields."

    yield Event(message=message)  # type: ignore[reportCallIssue]
    yield Event(route=route, output=route)  # type: ignore[reportCallIssue]


async def checking_details_user_message():
    """Tell user checking if worksheet/spreadsheet config details present"""
    yield Event(message="Checking if workbook/spreadsheet details configured yet...")  # type: ignore[reportCallIssue]


def router_2(node_input: str):
    return Event(route=node_input)  # type: ignore[reportCallIssue]


def successful_config_check_router(node_input: str):
    return Event(route=node_input)  # type: ignore[reportCallIssue]


MAX_CONFIG_LOAD_ATTEMPTS = 2


def load_config_into_context(ctx: Context):
    """Node 0f: load the previously-saved drive/workbook/sheet/header config
    from config.json into Context under the same state keys the interactive
    setup flow (handle_config_impl_agent.py) leaves behind, so downstream
    nodes can read consistent keys regardless of which branch ran.

    Retries once on failure before giving up: after MAX_CONFIG_LOAD_ATTEMPTS
    failed attempts it records ctx.state["config_load_error"] and routes to
    "Error" instead of "False", so the graph falls back to the node that
    performs config.json I/O directly rather than looping forever."""
    attempts = ctx.state.get("config_load_attempts", 0) + 1
    ctx.state["config_load_attempts"] = attempts

    msg = "Loaded workbook/sheet configuration from config.json."
    result = "True"
    try:
        data = json.loads(CONFIG_PATH.read_text())

        # Maps config.json's on-disk field names to the ctx.state keys used by
        # resolve_drive_selection/resolve_workbook_selection/resolve_sheet_selection.
        FIELD_TO_STATE_KEY = {
            "spreadsheet_id": "selected_workbook_id",
            "worksheet_name": "selected_sheet_name",
            "drive_id": "selected_drive_id",
            "working_dir": "working_dir",
            "sheet_headers": "sheet_headers",
        }

        for field, state_key in FIELD_TO_STATE_KEY.items():
            if field in data:
                ctx.state[state_key] = data[field]

        ctx.state["config_load_attempts"] = 0
    except Exception:
        if attempts < MAX_CONFIG_LOAD_ATTEMPTS:
            msg = "Error loading configuration, trying again..."
            result = "False"
        else:
            msg = (
                "Unable to load config.json into context after "
                f"{attempts} attempts. Falling back to reading config.json directly."
            )
            ctx.state["config_load_error"] = msg
            result = "Error"

    yield Event(message=msg)  # type: ignore[reportCallIssue]
    yield Event(output=result)


def successful_context_load_router(node_input: str):
    # "True" (config loaded) and "Error" (gave up retrying) both proceed to
    # the same next node; collapse them to one route value so the graph
    # doesn't see two edges to the same target (which validate_graph()
    # rejects as a duplicate edge, since it dedupes on (from, to) only,
    # not on route value).
    if node_input in ("True", "Error"):
        return Event(route="Proceed")  # type: ignore[reportCallIssue]
    return Event(route=node_input)  # type: ignore[reportCallIssue]


def job_url_fetch_result_router(node_input: str):
    if node_input == "DONE":
        return Event(route="DONE")  # type: ignore[reportCallIssue]
    else:
        return Event(route="RETRY")  # type: ignore[reportCallIssue]


response_job_agent = Workflow(
    # update this
    name="response_job_agent",
    edges=[
        (
            "START",
            checking_details_user_message,
            config_check_present_check,
            checking_config_check_result,
            router_2,
        ),
        (
            router_2,
            {
                "True": load_config_into_context,
                "False": response_handle_config_impl_node,
            },
        ),
        (load_config_into_context, successful_context_load_router),
        (response_handle_config_impl_node, successful_config_check_router),
        (
            successful_context_load_router,
            {
                "Proceed": response_job_url_fetch_node,
                "False": load_config_into_context,
            },
        ),
        (
            successful_config_check_router,
            {
                "True": response_job_url_fetch_node,
                "False": response_handle_config_impl_node,
            },
        ),
        (response_job_url_fetch_node, job_url_fetch_result_router),
        (
            job_url_fetch_result_router,
            {
                "RETRY": response_job_url_fetch_node,
                "DONE": job_url_fetch_done,
            },
        ),
    ],
)
