from google.adk.agents.context import Context
from google.adk.events import Event, RequestInput


def user_input_headers_to_clarify(node_input, ctx: Context):
    """Present the configured sheet headers so the user can pick which ones
    they want to give background/context on -- e.g. what counts as a "con"
    for them -- rather than leaving extract_job_spec_details_agent to guess
    an ambiguous header's meaning on its own."""
    headers = ctx.state.get("sheet_headers", [])
    yield RequestInput(
        message=(
            "Any headers you'd like to clarify the meaning of for extraction? "
            "(space to select, enter to confirm; leave empty to skip)"
        ),
        response_schema=list[str],
        payload={"choices": headers},
    )


def resolve_headers_to_clarify(node_input: list[str], ctx: Context):
    """Stash the user's selected headers as a queue for
    user_input_header_clarification to walk one at a time. Routes straight
    to write_config_file if the user picked none."""
    ctx.state["header_clarifications"] = {}
    ctx.state["headers_to_clarify_queue"] = list(node_input)

    if node_input:
        yield Event(route="clarify", output=node_input)  # type: ignore[reportCallIssue]
    else:
        yield Event(route="skip", output=node_input)  # type: ignore[reportCallIssue]


def user_input_header_clarification(node_input, ctx: Context):
    """Ask what the next queued header means to the user, one at a time --
    mirrors how user_input_folder_navigation walks the drive's folder tree
    via a ctx.state queue rather than multiple RequestInput yields in one
    node (a node completes on its first RequestInput; it doesn't resume
    mid-generator)."""
    queue = ctx.state.get("headers_to_clarify_queue", [])
    header = queue[0]
    ctx.state["current_clarifying_header"] = header
    yield RequestInput(
        message=f"What should '{header}' mean when extracting job posting details?",
        response_schema=str,
    )


def resolve_header_clarification(node_input: str, ctx: Context):
    """Record the clarification for the header currently being asked about,
    then either move on to the next queued header or, once the queue is
    empty, proceed to write_config_file."""
    header = ctx.state.get("current_clarifying_header")
    clarifications = dict(ctx.state.get("header_clarifications", {}))
    clarifications[header] = node_input
    ctx.state["header_clarifications"] = clarifications

    queue = list(ctx.state.get("headers_to_clarify_queue", []))
    if queue:
        queue.pop(0)
    ctx.state["headers_to_clarify_queue"] = queue

    if queue:
        yield Event(route="next", output=node_input)  # type: ignore[reportCallIssue]
    else:
        yield Event(route="done", output=node_input)  # type: ignore[reportCallIssue]
